from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from manage_section_draft import manage_draft  # noqa: E402
from save_section_draft import save_draft  # noqa: E402
from validate_section_draft import validate_draft  # noqa: E402


BLUEPRINTS = {
    "document_type": "feasibility_study",
    "blueprints": [
        {
            "section_role": "test_role",
            "purpose": "Test purpose",
            "required_questions": ["What is known?"],
            "required_fact_categories": [],
            "required_scope_types": [],
            "required_policy_topics": [],
            "required_tables": [],
            "forbidden_content": ["Fabrication"],
            "completion_rules": ["Traceable"],
        }
    ],
    "outline": [
        {
            "chapter_code": "1.1.1",
            "section_title": "Test Section",
            "section_role": "test_role",
        }
    ],
}


class SectionDraftVersionTests(unittest.TestCase):
    def test_versioning_adoption_restore_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-DRAFT-001"
            )
            database = Path(initialized["database"])
            plan_result = build_composition_plan(
                database, "TEST-DRAFT-001", blueprint_payload=BLUEPRINTS
            )
            self.assertEqual(plan_result["plans"][0]["status"], "ready")
            packages = root / "packages"
            export_packages(database, "TEST-DRAFT-001", packages)
            package = json.loads(next(packages.glob("*.json")).read_text(encoding="utf-8"))

            first = save_draft(
                database,
                "TEST-DRAFT-001",
                "1.1.1",
                "First draft.",
                package,
                provider="openai",
                model="test-model",
                prompt_version="v1",
            )
            duplicate = save_draft(
                database,
                "TEST-DRAFT-001",
                "1.1.1",
                "First draft.",
                package,
                provider="openai",
                model="test-model",
                prompt_version="v1",
            )
            second = save_draft(
                database,
                "TEST-DRAFT-001",
                "1.1.1",
                "Second draft.",
                package,
                provider="openai",
                model="test-model",
                prompt_version="v2",
            )
            self.assertTrue(first["created"])
            self.assertFalse(duplicate["created"])
            self.assertEqual(duplicate["version_no"], 1)
            self.assertEqual(second["version_no"], 2)

            validation = validate_draft(
                database, "TEST-DRAFT-001", "1.1.1", 2
            )
            self.assertEqual(validation["status"], "passed")

            adopted = manage_draft(
                database, "TEST-DRAFT-001", "1.1.1", 2, "adopt", operator="reviewer"
            )
            restored = manage_draft(
                database, "TEST-DRAFT-001", "1.1.1", 1, "restore", operator="reviewer"
            )
            self.assertEqual(adopted["plan_status"], "completed")
            self.assertEqual(restored["version_no"], 3)
            self.assertEqual(restored["plan_status"], "draft")

            conn = sqlite3.connect(database)
            try:
                rows = conn.execute(
                    "SELECT version_no,source_type,status,content FROM draft_section_version ORDER BY version_no"
                ).fetchall()
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[1][2], "adopted")
                self.assertEqual(rows[2][1], "restored")
                self.assertEqual(rows[2][3], "First draft.")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM llm_call_log").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 4)
            finally:
                conn.close()

    def test_unvalidated_or_thin_draft_cannot_be_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-DRAFT-GATE"
            )
            database = Path(initialized["database"])
            blueprints = json.loads(json.dumps(BLUEPRINTS))
            blueprints["blueprints"][0]["length_min"] = 30
            build_composition_plan(
                database, "TEST-DRAFT-GATE", blueprint_payload=blueprints
            )
            packages = Path(tmp) / "packages"
            export_packages(database, "TEST-DRAFT-GATE", packages)
            package = json.loads(next(packages.glob("*.json")).read_text(encoding="utf-8"))
            save_draft(
                database, "TEST-DRAFT-GATE", "1.1.1", "过短。", package,
                provider="openai", model="test-model", prompt_version="v1",
            )
            with self.assertRaises(RuntimeError):
                manage_draft(database, "TEST-DRAFT-GATE", "1.1.1", 1, "adopt")
            result = validate_draft(database, "TEST-DRAFT-GATE", "1.1.1", 1)
            self.assertEqual(result["status"], "failed")
            self.assertIn("section_too_short", {item["code"] for item in result["issues"]})
            with self.assertRaises(RuntimeError):
                manage_draft(database, "TEST-DRAFT-GATE", "1.1.1", 1, "adopt")

    def test_ai_draft_is_blocked_when_plan_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-DRAFT-BLOCKED"
            )
            database = Path(initialized["database"])
            build_composition_plan(database, "TEST-DRAFT-BLOCKED")
            packages = root / "packages"
            export_packages(database, "TEST-DRAFT-BLOCKED", packages)
            package_path = next(
                path
                for path in packages.glob("*.json")
                if json.loads(path.read_text(encoding="utf-8"))["plan"]["chapter_code"]
                == "1.1.1"
            )
            package = json.loads(package_path.read_text(encoding="utf-8"))

            with self.assertRaises(RuntimeError):
                save_draft(
                    database,
                    "TEST-DRAFT-BLOCKED",
                    "1.1.1",
                    "Should not save.",
                    package,
                    provider="openai",
                    model="test-model",
                    prompt_version="v1",
                )


if __name__ == "__main__":
    unittest.main()
