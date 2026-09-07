from __future__ import annotations

import os
import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).parent)]
from test_local_knowledge_packages import make_standard_pack, policy_catalog, write_json
from init_project_workbench import initialize_project
from knowledge_db import sha256_file
from knowledge_snapshot import KnowledgeSnapshotError
from local_knowledge_packages import LocalKnowledgeError, build_standard_package, build_policy_package
from local_knowledge_bootstrap import configure_packages, prepare_project_knowledge
from run_project_pipeline import run_pipeline


class LocalKnowledgeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.standard = self.root / "standard-knowledge.sqlite"
        self.policy = self.root / "policy-knowledge.sqlite"
        build_standard_package(make_standard_pack(self.root), self.standard, release_version="test")
        catalog = self.root / "catalog.json"
        write_json(catalog, policy_catalog())
        build_policy_package(self.policy, release_version="test", catalog_path=catalog)
        self.config = self.root / "user" / "local.json"
        self.digests = dict(standard_sha256=sha256_file(self.standard), policy_sha256=sha256_file(self.policy))

    def test_setup_is_idempotent_and_does_not_overwrite(self):
        self.assertEqual(configure_packages(self.root, config_path=self.config, **self.digests)["status"], "configured")
        self.assertEqual(configure_packages(self.root, config_path=self.config, **self.digests)["status"], "unchanged")
        with self.assertRaises(LocalKnowledgeError):
            configure_packages(self.root, config_path=self.config, **{**self.digests, "standard_sha256": "0"*64})
        write_json(self.config, {"user_existing": True})
        before = sha256_file(self.config)
        with self.assertRaises(FileExistsError):
            configure_packages(self.root, config_path=self.config, **self.digests)
        self.assertEqual(sha256_file(self.config), before)

    def test_pipeline_discovers_config_without_remote_calls(self):
        configure_packages(self.root, config_path=self.config, **self.digests)
        with patch.dict(os.environ, {"MEDICAL_REPORT_LOCAL_KB_CONFIG": str(self.config)}), \
             patch("run_project_pipeline.connect_postgres", side_effect=AssertionError("remote not allowed")):
            result = run_pipeline(self.root / "project", project_code="LOCAL", generate_working_drafts=False)
        knowledge = result["standard_knowledge"]
        self.assertEqual(knowledge["mode"], "snapshot_required")
        self.assertGreater(knowledge["manifest"]["counts"]["corpus_blocks"], 0)
        # Missing project facts and formal policies remain business blockers.
        self.assertNotEqual(result["status"], "completed")

    def test_invalid_local_config_blocks_instead_of_remote_fallback(self):
        with patch.dict(os.environ, {"MEDICAL_REPORT_LOCAL_KB_CONFIG": str(self.config)}), \
             patch("run_project_pipeline.connect_postgres", side_effect=AssertionError("remote not allowed")):
            result = run_pipeline(self.root / "project", project_code="MISSING", generate_working_drafts=False)
        self.assertIn("local_knowledge_preparation_failed", {b["reason"] for b in result["blockers"]})

    def test_existing_snapshot_is_kept_when_shared_package_changes(self):
        configure_packages(self.root, config_path=self.config, **self.digests)
        project = initialize_project(self.root / "project", project_code="PINNED")
        database = Path(project["database"])
        first = prepare_project_knowledge(self.config, database, "PINNED")
        newer = self.root / "new.sqlite"
        build_standard_package(make_standard_pack(self.root, suffix="new"), newer, release_version="new")
        import json
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        cfg["packages"]["standard"]["path"] = str(newer)
        write_json(self.config, cfg)
        second = prepare_project_knowledge(self.config, database, "PINNED")
        self.assertEqual(second["action"], "kept_project_snapshot")
        self.assertEqual(first["manifest"]["content_hashes"], second["manifest"]["content_hashes"])

    def test_permission_failure_rolls_back_new_snapshot(self):
        configure_packages(self.root, config_path=self.config, **self.digests)
        project = initialize_project(self.root / "project", project_code="DENIED")
        database = Path(project["database"])
        with closing(sqlite3.connect(database)) as conn:
            before = list(conn.iterdump())
        with self.assertRaises(KnowledgeSnapshotError):
            prepare_project_knowledge(self.config, database, "DENIED",
                                      selection={"permission_scopes": {"knowledge_package": ["public_only"]}})
        with closing(sqlite3.connect(database)) as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_explicit_project_mode_and_cli_mode_skip_local_discovery(self):
        for mode, cli in (("disabled", False), ("disabled", True), ("offline_pack", False),
                          ("snapshot_required", False)):
            with self.subTest(mode=mode, cli=cli):
                workbench = self.root / f"project-{mode}-{cli}"
                initialize_project(workbench, project_code="EXPLICIT")
                config_path = workbench / "project-config.json"
                project_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                project_config["knowledge"]["mode"] = "server_required" if cli else mode
                write_json(config_path, project_config)
                with patch.dict(os.environ, {"MEDICAL_REPORT_LOCAL_KB_CONFIG": str(self.config)}), \
                     patch("run_project_pipeline.prepare_project_knowledge",
                           side_effect=AssertionError("local discovery must be skipped")) as prepare:
                    result = run_pipeline(workbench, project_code="EXPLICIT", generate_working_drafts=False,
                                          knowledge_mode=mode if cli else None)
                prepare.assert_not_called()
                self.assertNotIn("local_knowledge_preparation_failed",
                                 {b["reason"] for b in result["blockers"]})


if __name__ == "__main__":
    unittest.main()
