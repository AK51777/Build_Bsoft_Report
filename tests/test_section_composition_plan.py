from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_section_composition_plan import build_composition_plan  # noqa: E402
from ingest_project_facts import ingest  # noqa: E402
from ingest_scope_items_sqlite import ingest_scope_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


class SectionCompositionPlanTests(unittest.TestCase):
    def test_builds_stable_three_level_plans_and_source_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="TEST-PLAN-001",
                official_name="Test Hospital Project",
            )
            database = Path(initialized["database"])
            ingest(
                database,
                {
                    "project": {
                        "project_code": "TEST-PLAN-001",
                        "official_name": "Test Hospital Project",
                        "document_type": "feasibility_study",
                    },
                    "facts": [
                        {
                            "fact_id": "FACT-PROJECT-NAME",
                            "fact_key": "project.official_name",
                            "fact_category": "project",
                            "fact_content": "Test Hospital Project",
                            "fact_status": "confirmed",
                            "materiality": "A",
                        },
                        {
                            "fact_id": "FACT-ACCEPTANCE",
                            "fact_key": "acceptance.emr_level",
                            "fact_category": "acceptance",
                            "fact_content": "Level 5",
                            "fact_status": "pending_confirmation",
                            "materiality": "A",
                        },
                    ],
                },
            )
            scope_payload = {
                "source": {"path": "C:/scope.xlsx", "sha256": "d" * 64},
                "sheets": [
                    {
                        "name": "Scope",
                        "detected_columns": {"original_name": ["建设内容"]},
                        "rows": [{"_source_row": 2, "建设内容": "电子病历系统"}],
                    }
                ],
            }
            scope_result = ingest_scope_payload(
                database, scope_payload, project_code="TEST-PLAN-001"
            )

            first = build_composition_plan(database, "TEST-PLAN-001")
            second = build_composition_plan(database, "TEST-PLAN-001")

            self.assertEqual(first["plan_count"], 28)
            self.assertEqual(
                {plan["plan_id"] for plan in first["plans"]},
                {plan["plan_id"] for plan in second["plans"]},
            )
            self.assertTrue(
                all(plan["chapter_code"].count(".") == 2 for plan in first["plans"])
            )
            overview = next(
                plan for plan in first["plans"] if plan["chapter_code"] == "1.1.1"
            )
            self.assertEqual(overview["status"], "blocked")
            self.assertIn("fact:acceptance", overview["missing_source_types"])
            self.assertIn("scope", overview["missing_source_types"])

            conn = sqlite3.connect(database)
            try:
                plan_sources = conn.execute(
                    """
                    SELECT source_type,source_object_id,usage_mode
                    FROM section_plan_source WHERE plan_id=?
                    """,
                    (overview["plan_id"],),
                ).fetchall()
                self.assertIn(("fact", "FACT-PROJECT-NAME", "direct"), plan_sources)
                self.assertIn(("fact", "FACT-ACCEPTANCE", "prohibited"), plan_sources)
                self.assertIn(
                    ("scope", scope_result["items"][0]["scope_id"], "prohibited"),
                    plan_sources,
                )
            finally:
                conn.close()

    def test_rebuild_preserves_completed_plan_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-PLAN-STATUS"
            )
            database = Path(initialized["database"])
            result = build_composition_plan(database, "TEST-PLAN-STATUS")
            plan_id = result["plans"][0]["plan_id"]
            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    "UPDATE section_composition_plan SET status='completed' WHERE plan_id=?",
                    (plan_id,),
                )
                conn.commit()
            finally:
                conn.close()

            rebuilt = build_composition_plan(database, "TEST-PLAN-STATUS")
            rebuilt_plan = next(plan for plan in rebuilt["plans"] if plan["plan_id"] == plan_id)
            self.assertEqual(rebuilt_plan["status"], "completed")


if __name__ == "__main__":
    unittest.main()
