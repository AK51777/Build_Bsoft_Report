from __future__ import annotations

import sqlite3
import inspect
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ingest_product_capabilities import ingest_capabilities  # noqa: E402
from ingest_scope_items_sqlite import ingest_scope_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from map_scope_capabilities import map_capabilities  # noqa: E402
from validate_project_gates import validate  # noqa: E402


class ScopeCapabilityMappingTests(unittest.TestCase):
    def test_default_similarity_threshold_is_conservative(self) -> None:
        self.assertEqual(inspect.signature(map_capabilities).parameters["threshold"].default, 0.55)

    def test_exact_product_scope_expands_to_all_product_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-MAP-PRODUCT"
            )
            database = Path(initialized["database"])
            ingest_scope_payload(
                database,
                {
                    "source": {"path": "C:/scope.xlsx", "sha256": "d" * 64},
                    "sheets": [{
                        "name": "Scope",
                        "detected_columns": {"original_name": ["建设内容"]},
                        "rows": [{"_source_row": 2, "建设内容": "医院数据中心"}],
                    }],
                },
                project_code="TEST-MAP-PRODUCT",
            )
            ingest_capabilities(
                database,
                {
                    "capabilities": [
                        {
                            "product_code": "DC",
                            "product_name": "医院数据中心",
                            "capability_name": "临床数据中心",
                            "capability_description": "临床数据归集",
                            "review_status": "approved",
                        },
                        {
                            "product_code": "DC",
                            "product_name": "医院数据中心",
                            "capability_name": "管理数据中心",
                            "capability_description": "管理数据归集",
                            "review_status": "approved",
                        },
                    ]
                },
            )
            result = map_capabilities(
                database, "TEST-MAP-PRODUCT", max_candidates=1
            )
            self.assertEqual(len(result["candidates"]), 2)
            self.assertTrue(all(
                item["match_basis"] == "exact_product" for item in result["candidates"]
            ))

    def test_mapping_never_creates_scope_and_preserves_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-MAP-001"
            )
            database = Path(initialized["database"])
            payload = {
                "source": {"path": "C:/scope.xlsx", "sha256": "b" * 64},
                "sheets": [
                    {
                        "name": "Scope",
                        "detected_columns": {"original_name": ["建设内容"]},
                        "rows": [{"_source_row": 2, "建设内容": "电子病历系统"}],
                    }
                ],
            }
            ingest_scope_payload(database, payload, project_code="TEST-MAP-001")
            ingest_capabilities(
                database,
                {
                    "capabilities": [
                        {
                            "product_code": "EMR",
                            "product_name": "电子病历",
                            "capability_name": "电子病历系统",
                            "capability_description": "支持电子病历管理",
                            "review_status": "approved",
                        },
                        {
                            "product_code": "HRP",
                            "product_name": "运营管理",
                            "capability_name": "财务管理",
                            "capability_description": "支持预算和核算",
                            "review_status": "approved",
                        },
                    ]
                },
            )
            first = map_capabilities(database, "TEST-MAP-001")
            self.assertEqual(len(first["candidates"]), 1)
            self.assertEqual(first["candidates"][0]["mapping_type"], "one_to_one")

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM project_scope_item").fetchone()[0], 1
                )
                map_id = first["candidates"][0]["map_id"]
                conn.execute(
                    "UPDATE scope_product_map SET status='confirmed',review_note='人工确认' WHERE map_id=?",
                    (map_id,),
                )
                conn.commit()
            finally:
                conn.close()

            map_capabilities(database, "TEST-MAP-001", threshold=0.1)
            conn = sqlite3.connect(database)
            try:
                row = conn.execute(
                    "SELECT status,review_note FROM scope_product_map WHERE map_id=?",
                    (map_id,),
                ).fetchone()
                self.assertEqual(row, ("confirmed", "人工确认"))
            finally:
                conn.close()

    def test_scope_gate_reports_registered_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-MAP-GATE"
            )
            database = Path(initialized["database"])
            before = validate(database, "TEST-MAP-GATE")
            before_by_code = {item["gate_code"]: item for item in before["checks"]}
            self.assertEqual(before_by_code["GATE-SCOPE-REGISTERED"]["result"], "fail")

            payload = {
                "source": {"path": "C:/scope.xlsx", "sha256": "c" * 64},
                "sheets": [
                    {
                        "name": "Scope",
                        "detected_columns": {"original_name": ["建设内容"]},
                        "rows": [{"_source_row": 2, "建设内容": "集成平台"}],
                    }
                ],
            }
            ingest_scope_payload(database, payload, project_code="TEST-MAP-GATE")
            after = validate(database, "TEST-MAP-GATE")
            after_by_code = {item["gate_code"]: item for item in after["checks"]}
            self.assertEqual(after_by_code["GATE-SCOPE-REGISTERED"]["result"], "pass")
            self.assertEqual(after_by_code["GATE-SCOPE-BOUNDARY"]["result"], "warning")


if __name__ == "__main__":
    unittest.main()
