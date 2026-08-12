from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_scope_baseline import build_scope_baseline  # noqa: E402
from build_traceability_matrix import build_traceability_matrix  # noqa: E402
from confirm_scope_baseline import confirm_scope_baseline  # noqa: E402
from ingest_project_facts import ingest  # noqa: E402
from ingest_scope_items_sqlite import ingest_scope_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from validate_project_gates import validate  # noqa: E402


class ScopeBaselineTraceabilityTests(unittest.TestCase):
    def test_baseline_confirmation_and_complete_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-TRACE-001"
            )
            database = Path(initialized["database"])
            scope_result = ingest_scope_payload(
                database,
                {
                    "source": {"path": "C:/scope.xlsx", "sha256": "e" * 64},
                    "sheets": [
                        {
                            "name": "Scope",
                            "detected_columns": {
                                "original_name": ["建设内容"],
                                "item_type": ["费用类型"],
                                "acceptance_target": ["验收目标"],
                            },
                            "rows": [
                                {
                                    "_source_row": 2,
                                    "建设内容": "电子病历系统",
                                    "费用类型": "软件",
                                    "验收目标": "完成验收测试",
                                }
                            ],
                        }
                    ],
                },
                project_code="TEST-TRACE-001",
            )
            scope_id = scope_result["items"][0]["scope_id"]
            pending = build_scope_baseline(database, "TEST-TRACE-001")
            self.assertEqual(pending["counts"]["pending"], 1)
            with self.assertRaises(RuntimeError):
                confirm_scope_baseline(
                    database,
                    "TEST-TRACE-001",
                    pending["baseline_id"],
                    confirmed_by="reviewer",
                    confirmed_at="2026-08-11T10:00:00+08:00",
                )

            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    "UPDATE project_scope_item SET status='confirmed',chapter_location='5.1.1' WHERE scope_id=?",
                    (scope_id,),
                )
                conn.commit()
            finally:
                conn.close()
            confirmed_candidate = build_scope_baseline(database, "TEST-TRACE-001")
            self.assertNotEqual(confirmed_candidate["baseline_id"], pending["baseline_id"])
            confirmation = confirm_scope_baseline(
                database,
                "TEST-TRACE-001",
                confirmed_candidate["baseline_id"],
                confirmed_by="reviewer",
                confirmed_at="2026-08-11T10:05:00+08:00",
            )
            duplicate = confirm_scope_baseline(
                database,
                "TEST-TRACE-001",
                confirmed_candidate["baseline_id"],
                confirmed_by="reviewer",
                confirmed_at="2026-08-11T10:05:00+08:00",
            )
            self.assertTrue(confirmation["created"])
            self.assertFalse(duplicate["created"])

            fact_ids = {
                "problem_fact_id": "FACT-PROBLEM",
                "requirement_fact_id": "FACT-REQUIREMENT",
                "investment_fact_id": "FACT-INVESTMENT",
                "indicator_fact_id": "FACT-INDICATOR",
                "benefit_fact_id": "FACT-BENEFIT",
            }
            ingest(
                database,
                {
                    "project": {
                        "project_code": "TEST-TRACE-001",
                        "official_name": "Traceability Test",
                    },
                    "facts": [
                        {
                            "fact_id": fact_id,
                            "fact_key": fact_id.lower().replace("fact-", "") + ".value",
                            "fact_content": fact_id,
                            "fact_status": "confirmed",
                        }
                        for fact_id in fact_ids.values()
                    ],
                },
            )
            incomplete = build_traceability_matrix(database, "TEST-TRACE-001")
            self.assertEqual(incomplete["incomplete_count"], 1)
            complete = build_traceability_matrix(
                database,
                "TEST-TRACE-001",
                links_payload={
                    "links": [
                        {
                            "scope_id": scope_id,
                            **fact_ids,
                            "chapter_location": "5.1.1",
                            "evaluation_method": "验收测试记录",
                            "expected_benefit": "形成可追溯的预期效益",
                        }
                    ]
                },
            )
            repeated = build_traceability_matrix(
                database,
                "TEST-TRACE-001",
                links_payload={
                    "links": [{"scope_id": scope_id, **fact_ids, "chapter_location": "5.1.1"}]
                },
            )
            self.assertEqual(complete["complete_count"], 1)
            self.assertEqual(
                complete["rows"][0]["traceability_id"],
                repeated["rows"][0]["traceability_id"],
            )
            gates = validate(database, "TEST-TRACE-001")
            gate_by_code = {item["gate_code"]: item for item in gates["checks"]}
            self.assertEqual(gate_by_code["GATE-SCOPE-BASELINE"]["result"], "pass")
            self.assertEqual(gate_by_code["GATE-TRACEABILITY-MATRIX"]["result"], "pass")


if __name__ == "__main__":
    unittest.main()
