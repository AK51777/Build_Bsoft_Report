from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from init_project_workbench import initialize_project  # noqa: E402
from import_standard_knowledge_pack import import_pack  # noqa: E402
from knowledge_doctor import diagnose_settings  # noqa: E402
from knowledge_profile import public_settings, resolve_knowledge_settings  # noqa: E402
from knowledge_snapshot import KnowledgeSnapshotError, validate_snapshots  # noqa: E402
from knowledge_db import connect, dump_json, sha256_text  # noqa: E402
from query_local_knowledge import query_local  # noqa: E402
from run_project_pipeline import run_pipeline  # noqa: E402
from sync_postgres_knowledge_snapshot import (  # noqa: E402
    KnowledgeSelectionError,
    record_snapshot,
    select_packages,
)


def profile_payload(host: str, *, default_profile: str = "default") -> dict:
    return {
        "schema_version": "1.0",
        "default_profile": default_profile,
        "profiles": {
            "default": {
                "host": host,
                "port": 15432,
                "database": "medical_kb",
                "user": "kb_reader",
                "password_env": "TEST_MEDICAL_KB_PASSWORD",
                "schema": "medical_report_kb",
                "mode": "server_required",
                "package_ids": ["PACK-DEFAULT"],
                "catalog_ids": ["CAT-DEFAULT"],
            },
            "secondary": {
                "host": f"{host}-secondary",
                "port": 25432,
                "database": "medical_kb",
                "user": "kb_reader",
                "password_env": "TEST_MEDICAL_KB_PASSWORD",
                "schema": "medical_report_kb",
                "mode": "server_required",
                "package_ids": ["PACK-SECONDARY"],
                "catalog_ids": ["CAT-SECONDARY"],
            },
        },
    }


class Column:
    def __init__(self, name: str):
        self.name = name


class CandidateCursor:
    def __init__(self, packages: list[dict], documents: dict[str, dict]):
        self.packages = packages
        self.documents = documents
        self.rows = []
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        if "runtime_knowledge_package" in statement:
            ids = set((params or [[]])[0]) if "ANY" in statement else None
            values = [row for row in self.packages if ids is None or row["package_id"] in ids]
        elif "runtime_corpus_document" in statement:
            item = self.documents.get((params or [""])[0])
            values = [item] if item else []
        elif "runtime_corpus_block" in statement:
            values = [{"section_role": "construction_content", "module_code": "clinical"}]
        else:
            raise AssertionError(statement)
        keys = list(values[0]) if values else ["package_id"]
        self.description = [Column(key) for key in keys]
        self.rows = [tuple(value.get(key) for key in keys) for value in values]

    def fetchall(self):
        return list(self.rows)


class CandidateConnection:
    def __init__(self, packages: list[dict], documents: dict[str, dict]):
        self.packages = packages
        self.documents = documents

    def cursor(self):
        return CandidateCursor(self.packages, self.documents)


class KnowledgeRuntimeTests(unittest.TestCase):
    def test_profile_path_and_profile_selection_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / ".codex" / "config" / "medical-report-kb.json"
            env_path = root / "env.json"
            explicit_path = root / "explicit.json"
            default_path.parent.mkdir(parents=True)
            default_path.write_text(json.dumps(profile_payload("default-host")), encoding="utf-8")
            env_path.write_text(json.dumps(profile_payload("env-host")), encoding="utf-8")
            explicit_path.write_text(json.dumps(profile_payload("explicit-host")), encoding="utf-8")
            project = {
                "project": {"document_type": "feasibility_study", "project_type": "hospital_informationization"},
                "knowledge": {"mode": "server_required", "profile": "secondary"},
            }
            settings = resolve_knowledge_settings(
                project,
                explicit_config_path=explicit_path,
                environ={"MEDICAL_FEASIBILITY_KB_CONFIG": str(env_path)},
                home=root,
            )
            self.assertEqual(settings["host"], "explicit-host-secondary")
            self.assertEqual(settings["profile_name"], "secondary")
            self.assertEqual(settings["config_source"], "command_line")

            settings = resolve_knowledge_settings(
                project,
                explicit_profile="default",
                environ={"MEDICAL_FEASIBILITY_KB_CONFIG": str(env_path)},
                home=root,
            )
            self.assertEqual(settings["host"], "env-host")
            self.assertEqual(settings["profile_name"], "default")
            self.assertEqual(settings["config_source"], "environment")

    def test_password_is_never_emitted(self) -> None:
        settings = {
            "mode": "server_required",
            "profile_name": "default",
            "config_path": "profile.json",
            "config_source": "command_line",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "kb",
            "user": "reader",
            "schema": "medical_report_kb",
            "password_env": "TEST_MEDICAL_KB_PASSWORD",
            "connect_timeout": 1,
            "package_ids": [],
            "catalog_ids": [],
            "policy_topics": [],
            "permission_scopes": {},
            "allow_stale_cache": False,
            "selection": {},
        }
        env = {"TEST_MEDICAL_KB_PASSWORD": "super-secret-value"}
        public = public_settings(settings, environ=env)
        doctor = diagnose_settings(settings, environ={})
        rendered = json.dumps({"public": public, "doctor": doctor}, ensure_ascii=False)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn('"password":', rendered.casefold())
        self.assertEqual(doctor["exit_code"], 11)

    def test_multiple_matching_packages_are_listed_and_blocked(self) -> None:
        packages = [
            {
                "package_id": package_id,
                "title": title,
                "schema_version": "1.0",
                "permission_scope": "internal_company_reuse",
                "content_hash": hash_char * 64,
                "published_at": "2026-08-01",
            }
            for package_id, title, hash_char in (
                ("PACK-A", "A方案", "a"),
                ("PACK-B", "B方案", "b"),
            )
        ]
        documents = {
            package["package_id"]: {
                "package_id": package["package_id"],
                "corpus_document_id": f"DOC-{package['package_id']}",
                "document_type": "feasibility_study",
                "project_type": "hospital_informationization",
                "jurisdiction_code": "",
                "version": "1.0",
            }
            for package in packages
        }
        connection = CandidateConnection(packages, documents)
        with self.assertRaises(KnowledgeSelectionError) as raised:
            select_packages(
                connection,
                "medical_report_kb",
                [],
                criteria={"document_type": "feasibility_study", "project_type": "hospital_informationization"},
                permission_scopes=["internal_company_reuse"],
            )
        self.assertEqual(raised.exception.reason, "knowledge_package_ambiguous")
        self.assertEqual([item["package_id"] for item in raised.exception.candidates], ["PACK-A", "PACK-B"])

    def test_no_published_package_is_blocked(self) -> None:
        with self.assertRaises(KnowledgeSelectionError) as raised:
            select_packages(
                CandidateConnection([], {}),
                "medical_report_kb",
                [],
                criteria={"project_type": "hospital_informationization"},
                permission_scopes=["internal_company_reuse"],
            )
        self.assertEqual(raised.exception.reason, "knowledge_package_not_found")

    def test_permission_scope_mismatch_is_blocked_with_candidate(self) -> None:
        package = {
            "package_id": "PACK-PRIVATE",
            "title": "受限知识包",
            "schema_version": "1.0",
            "permission_scope": "restricted_tenant",
            "content_hash": "d" * 64,
            "published_at": "2026-08-01",
        }
        document = {
            "package_id": "PACK-PRIVATE",
            "corpus_document_id": "DOC-PRIVATE",
            "document_type": "feasibility_study",
            "project_type": "hospital_informationization",
            "jurisdiction_code": "",
            "version": "1.0",
        }
        with self.assertRaises(KnowledgeSelectionError) as raised:
            select_packages(
                CandidateConnection([package], {"PACK-PRIVATE": document}),
                "medical_report_kb",
                [],
                criteria={"project_type": "hospital_informationization"},
                permission_scopes=["internal_company_reuse"],
            )
        self.assertEqual(raised.exception.reason, "knowledge_package_candidate_missing")
        self.assertEqual(raised.exception.candidates[0]["permission_scope"], "restricted_tenant")

    def test_server_connection_failure_has_stable_exit_code(self) -> None:
        settings = {
            "mode": "server_required",
            "profile_name": "default",
            "config_path": "profile.json",
            "config_source": "command_line",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "kb",
            "user": "reader",
            "schema": "medical_report_kb",
            "password_env": "TEST_MEDICAL_KB_PASSWORD",
            "connect_timeout": 1,
            "package_ids": [],
            "catalog_ids": [],
            "policy_topics": [],
            "permission_scopes": {},
            "allow_stale_cache": False,
            "selection": {},
        }

        def fail_connect(_args):
            raise RuntimeError("connection refused with secret not echoed")

        result = diagnose_settings(
            settings,
            connector=fail_connect,
            tcp_probe=None,
            environ={"TEST_MEDICAL_KB_PASSWORD": "never-output-this"},
        )
        self.assertEqual(result["exit_code"], 13)
        self.assertEqual(result["failure_class"], "connection")
        self.assertNotIn("never-output-this", json.dumps(result))

    def test_snapshot_validation_and_offline_query_keep_block_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            initialized = initialize_project(project, project_code="SNAP-RUNTIME")
            database = Path(initialized["database"])
            block_text = "电子病历系统应形成患者身份、医嘱、执行、结果和质量控制闭环。"
            block_hash = sha256_text(block_text)
            capability = {
                "capability_id": "CAP-001",
                "product_code": "EMR",
                "product_name": "电子病历系统",
                "capability_name": "临床业务闭环",
                "capability_description": "支持临床业务闭环。",
                "standard_block_ids": ["BLOCK-001"],
                "review_status": "approved",
            }
            pack = {
                "schema_version": "1.0",
                "package_id": "PACK-001",
                "title": "测试标准知识包",
                "permission_scope": "internal_company_reuse",
                "source_files": [
                    {
                        "role": "standard_solution",
                        "file_name": "company-standard.docx",
                        "sha256": "c" * 64,
                    }
                ],
                "corpus": {
                    "document_id": "DOC-PACK-001",
                    "document_type": "feasibility_study",
                    "project_type": "hospital_informationization",
                    "version": "2026.1",
                    "quality_level": "B",
                    "review_status": "approved",
                    "blocks": [
                        {
                            "block_id": "BLOCK-001",
                            "source_location": "建设内容 / 电子病历系统",
                            "heading_path": ["建设内容", "电子病历系统"],
                            "section_role": "construction_content",
                            "module_code": "EMR",
                            "clean_text": block_text,
                            "reuse_class": "B",
                            "quality_level": "B",
                            "review_status": "approved",
                            "prerequisites": [],
                            "variable_slots": [],
                            "forbidden_terms": [],
                        }
                    ],
                },
                "capabilities": [capability],
            }
            import_pack(database, pack)
            record_snapshot(
                database,
                "SNAP-RUNTIME",
                source_type="knowledge_package",
                source_id="PACK-001",
                content_hash="a" * 64,
                server_schema="medical_report_kb",
                items=[
                    (
                        "corpus_block",
                        "BLOCK-001",
                        block_hash,
                        {
                            "block_id": "BLOCK-001",
                            "clean_text": block_text,
                            "text_hash": block_hash,
                        },
                    ),
                    (
                        "product_capability",
                        "CAP-001",
                        sha256_text(dump_json(capability)),
                        capability,
                    ),
                ],
                metadata={"blocks": 1, "capabilities": 1},
                permission_scope="internal_company_reuse",
                profile_name="default",
            )
            valid = validate_snapshots(
                database,
                "SNAP-RUNTIME",
                package_ids=["PACK-001"],
                permission_scopes={"knowledge_package": ["internal_company_reuse"]},
            )
            self.assertEqual(valid["counts"]["corpus_blocks"], 1)
            online_rows = query_local(
                database,
                package_ids=["PACK-001"],
                applicable_version="2026.1",
                search="质量控制闭环",
            )["rows"]
            self.assertEqual([item["block_id"] for item in online_rows], ["BLOCK-001"])
            with connect(database) as connection:
                connection.execute(
                    "UPDATE shared_knowledge_snapshot SET snapshot_status='stale' WHERE source_id='PACK-001'"
                )
                connection.commit()
            with self.assertRaises(KnowledgeSnapshotError):
                validate_snapshots(database, "SNAP-RUNTIME", package_ids=["PACK-001"])
            stale = validate_snapshots(
                database,
                "SNAP-RUNTIME",
                package_ids=["PACK-001"],
                permission_scopes={"knowledge_package": ["internal_company_reuse"]},
                allow_stale=True,
            )
            self.assertEqual(stale["package_ids"], ["PACK-001"])
            offline_rows = query_local(
                database,
                package_ids=["PACK-001"],
                applicable_version="2026.1",
                search="质量控制闭环",
            )["rows"]
            self.assertEqual(
                [item["block_id"] for item in offline_rows],
                [item["block_id"] for item in online_rows],
            )

    def test_zero_count_and_incomplete_snapshot_metadata_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            initialized = initialize_project(project, project_code="SNAP-INVALID")
            database = Path(initialized["database"])
            capability = {
                "capability_id": "CAP-ONLY",
                "capability_name": "孤立能力",
            }
            snapshot_id = record_snapshot(
                database,
                "SNAP-INVALID",
                source_type="knowledge_package",
                source_id="PACK-EMPTY",
                content_hash="e" * 64,
                server_schema="medical_report_kb",
                items=[
                    (
                        "product_capability",
                        "CAP-ONLY",
                        sha256_text(dump_json(capability)),
                        capability,
                    )
                ],
                metadata={"blocks": 0, "capabilities": 1},
                permission_scope="internal_company_reuse",
            )
            with self.assertRaises(KnowledgeSnapshotError) as zero_error:
                validate_snapshots(database, "SNAP-INVALID", package_ids=["PACK-EMPTY"])
            self.assertIn("zero corpus blocks", json.dumps(zero_error.exception.details))

            with connect(database) as connection:
                metadata = json.loads(
                    connection.execute(
                        "SELECT metadata_json FROM shared_knowledge_snapshot WHERE snapshot_id=?",
                        (snapshot_id,),
                    ).fetchone()[0]
                )
                metadata.pop("snapshot_payload_hash")
                metadata.pop("sync_completed")
                connection.execute(
                    "UPDATE shared_knowledge_snapshot SET metadata_json=? WHERE snapshot_id=?",
                    (dump_json(metadata), snapshot_id),
                )
                connection.commit()
            with self.assertRaises(KnowledgeSnapshotError) as metadata_error:
                validate_snapshots(database, "SNAP-INVALID", package_ids=["PACK-EMPTY"])
            rendered = json.dumps(metadata_error.exception.details)
            self.assertIn("completed synchronization", rendered)
            self.assertIn("aggregate payload hash", rendered)

    def test_server_pipeline_passes_catalog_ids_and_records_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "profile.json"
            config_path.write_text(json.dumps(profile_payload("127.0.0.1")), encoding="utf-8")
            sync_result = {
                "database": str(root / "project" / "数据包" / "数据库" / "knowledge.sqlite"),
                "project_code": "PIPE-CATALOG",
                "knowledge_packages": [],
                "policy_catalogs": [],
                "policy": {"policies": 0, "clauses": 0, "snapshot_id": ""},
                "server_schema": "medical_report_kb",
                "selected_package_ids": ["PACK-DEFAULT"],
                "selected_catalog_ids": ["CAT-DEFAULT"],
                "snapshot_validation": {
                    "status": "valid",
                    "snapshot_count": 2,
                    "package_ids": ["PACK-DEFAULT"],
                    "catalog_ids": ["CAT-DEFAULT"],
                    "content_hashes": {"PACK-DEFAULT": "a" * 64, "CAT-DEFAULT": "b" * 64},
                    "counts": {"corpus_blocks": 10, "capabilities": 3, "catalog_records": 5, "policy_clauses": 0},
                    "snapshots": [],
                },
            }
            doctor = {"status": "passed", "failure_class": "", "checks": [], "counts": {}}
            with patch.dict(os.environ, {"TEST_MEDICAL_KB_PASSWORD": "not-logged"}, clear=False), patch(
                "run_project_pipeline.diagnose_settings", return_value=doctor
            ), patch(
                "run_project_pipeline.connect_postgres", return_value=nullcontext(object())
            ), patch(
                "run_project_pipeline.sync_postgres_knowledge", return_value=sync_result
            ) as sync_mock:
                result = run_pipeline(
                    root / "project",
                    project_code="PIPE-CATALOG",
                    official_name="目录传参测试项目",
                    knowledge_config_path=config_path,
                    knowledge_profile="default",
                    generate_working_drafts=False,
                )
            self.assertEqual(sync_mock.call_args.kwargs["catalog_ids"], ["CAT-DEFAULT"])
            self.assertEqual(result["standard_knowledge"]["manifest"]["counts"]["corpus_blocks"], 10)
            project_config = json.loads((root / "project" / "project-config.json").read_text(encoding="utf-8"))
            self.assertEqual(project_config["knowledge"]["catalog_ids"], ["CAT-DEFAULT"])
            self.assertNotIn("not-logged", json.dumps(result, ensure_ascii=False))

    def test_server_required_missing_profile_stops_at_s0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_pipeline(
                root / "project",
                project_code="PIPE-BLOCKED",
                official_name="知识门禁测试项目",
                knowledge_config_path=root / "missing-profile.json",
                knowledge_mode="server_required",
            )
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(result["stage_results"][0]["status"], "blocked")
            self.assertTrue(all(item["status"] == "not_started" for item in result["stage_results"][1:]))
            self.assertFalse((root / "project" / "11-正文工作稿" / "report-working.md").exists())

    def test_local_pack_cannot_be_imported_while_knowledge_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_pipeline(
                root / "project",
                project_code="PIPE-DISABLED-PACK",
                official_name="禁用模式边界测试项目",
                knowledge_mode="disabled",
                standard_knowledge_packs=[root / "not-read.json"],
            )
            reasons = {item["reason"] for item in result["blockers"]}
            self.assertIn("offline_pack_mode_required", reasons)
            self.assertEqual(result["standard_knowledge"]["manifest"]["source"], "none")
            self.assertFalse((root / "project" / "11-正文工作稿" / "report-working.md").exists())


if __name__ == "__main__":
    unittest.main()
