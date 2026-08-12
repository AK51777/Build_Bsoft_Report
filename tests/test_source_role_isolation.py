from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_candidate_fact_workpack import build_workpack as build_fact_workpack  # noqa: E402
from build_reference_reuse_workpack import build_workpack as build_reference_workpack  # noqa: E402
from classify_source_roles import classify_inventory  # noqa: E402
from extract_clean_document_blocks import build_payload  # noqa: E402
from ingest_clean_documents_sqlite import ingest_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


class SourceRoleIsolationTests(unittest.TestCase):
    def test_reference_text_is_not_a_candidate_project_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(root / "project", project_code="ROLE-001")
            database = Path(initialized["database"])
            project_source = root / "院方材料.md"
            reference_source = root / "外地参考可研.md"
            project_source.write_text("# 现状\n\n目标项目材料。\n", encoding="utf-8")
            reference_source.write_text("# 投资\n\n参考项目金额不得继承。\n", encoding="utf-8")

            ingest_payload(
                database,
                build_payload(project_source, "ROLE-001", project_source.stem, False),
                source_class="project_material",
            )
            ingest_payload(
                database,
                build_payload(reference_source, "ROLE-001", reference_source.stem, False),
                source_class="external_reference",
            )

            facts = build_fact_workpack(database, "ROLE-001")
            references = build_reference_workpack(database, "ROLE-001")
            fact_text = "\n".join(block["clean_text"] for block in facts["source_blocks"])
            self.assertIn("目标项目材料", fact_text)
            self.assertNotIn("参考项目金额", fact_text)
            self.assertEqual(references["reference_source_count"], 1)
            self.assertEqual(references["status"], "pending_confirmation")

    def test_explicit_role_override_beats_keyword_heuristic(self) -> None:
        inventory = {
            "files": [
                {
                    "source_id": "SRC-001",
                    "relative_path": "院方确认/参考命名但属于项目材料.md",
                    "sha256": "abc",
                }
            ]
        }
        result = classify_inventory(
            inventory,
            overrides={"院方确认/*": "project_material"},
        )
        self.assertEqual(result["sources"][0]["role"], "project_material")
        self.assertEqual(result["sources"][0]["classification_basis"], "explicit_override")
        self.assertFalse(result["sources"][0]["needs_review"])


if __name__ == "__main__":
    unittest.main()
