from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document
from openpyxl import Workbook


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from apply_scope_capability_decisions import apply_decisions  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from build_standard_knowledge_pack import build_pack  # noqa: E402
from build_standard_knowledge_pack import section_role  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from import_standard_knowledge_pack import import_pack  # noqa: E402
from ingest_scope_items_sqlite import ingest_scope_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from map_scope_capabilities import map_capabilities  # noqa: E402


def make_standard_files(root: Path) -> tuple[Path, Path]:
    solution = root / "company-standard.docx"
    document = Document()
    document.add_heading("建设内容", level=1)
    document.add_heading("电子病历系统", level=2)
    document.add_paragraph(
        "电子病历系统围绕临床文书、病历质控、数据共享和闭环管理形成业务能力。"
        "方案正文只可在客户已确认范围内按项目事实参数化改写，不得反向扩展建设范围。" * 4
    )
    document.add_heading("政策法规", level=1)
    document.add_paragraph(
        "本段仅为历史方案中列示的政策文字，必须重新核验现行状态、官方来源和适用条款。" * 4
    )
    document.save(solution)

    scope = root / "company-scope.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主表"
    sheet.append(["大类", "系统名称", "模块名称", "产品功能", "三级医院推荐"])
    sheet.append(["临床业务", "电子病历系统", "电子病历系统", "病历质控", "推荐"])
    workbook.save(scope)
    return solution, scope


class StandardKnowledgePackTests(unittest.TestCase):
    def test_build_import_map_and_recall_are_bounded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            solution, scope = make_standard_files(root)
            payload = build_pack(solution, scope, title="测试标准知识包")
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(root), serialized)
            self.assertEqual(payload["permission_scope"], "internal_company_reuse")
            self.assertEqual(section_role(["政策法规"]), "policy")
            policy_blocks = [
                block for block in payload["corpus"]["blocks"]
                if block["section_role"] == "policy"
            ]
            self.assertTrue(policy_blocks)
            self.assertTrue(all(block["review_status"] == "prohibited" for block in policy_blocks))
            self.assertTrue(payload["capabilities"][0]["standard_block_ids"])

            initialized = initialize_project(
                root / "project", project_code="PACK-TEST-001",
                official_name="标准知识包测试项目", owner_name="测试医院",
            )
            database = Path(initialized["database"])
            first = import_pack(database, payload)
            second = import_pack(database, payload)
            self.assertEqual(first["blocks_imported"], second["blocks_imported"])

            ingest_scope_payload(
                database,
                {
                    "source": {"path": "scope.xlsx", "sha256": "a" * 64},
                    "sheets": [{
                        "name": "建设清单",
                        "detected_columns": {"original_name": ["建设内容"]},
                        "rows": [{"_source_row": 2, "建设内容": "电子病历系统"}],
                    }],
                },
                project_code="PACK-TEST-001",
            )
            conn = sqlite3.connect(database)
            try:
                conn.execute("UPDATE project_scope_item SET status='confirmed'")
                conn.commit()
            finally:
                conn.close()

            mapping = map_capabilities(database, "PACK-TEST-001")
            self.assertEqual(len(mapping["candidates"]), 1)
            apply_decisions(
                database,
                {
                    "project_code": "PACK-TEST-001",
                    "reviewed_by": "reviewer",
                    "decisions": [{
                        "map_id": mapping["candidates"][0]["map_id"],
                        "decision": "confirmed",
                        "review_note": "客户范围与标准能力一致",
                    }],
                },
            )
            plan_result = build_composition_plan(database, "PACK-TEST-001")
            self.assertEqual(plan_result["not_applicable_count"], 3)
            construction = next(
                item for item in plan_result["plans"] if item["chapter_code"] == "5.1.1"
            )
            self.assertNotIn("capability_mapping_review", construction["missing_source_types"])
            self.assertGreaterEqual(construction["outline_node_count"], 3)
            self.assertEqual(construction["outline_heading_levels"]["4"], 1)
            self.assertEqual(construction["outline_heading_levels"]["5"], 1)

            conn = sqlite3.connect(database)
            try:
                plan_id = conn.execute(
                    "SELECT plan_id FROM section_composition_plan WHERE project_id=(SELECT project_id FROM project WHERE project_code=?) AND chapter_code='5.1.1'",
                    ("PACK-TEST-001",),
                ).fetchone()[0]
                source_types = conn.execute(
                    "SELECT source_type,usage_mode FROM section_plan_source WHERE plan_id=?",
                    (plan_id,),
                ).fetchall()
                self.assertIn(("capability", "parameterized"), source_types)
                self.assertIn(("corpus", "parameterized"), source_types)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_scope_item").fetchone()[0], 1)
            finally:
                conn.close()

            packages = root / "packages"
            exported = export_packages(database, "PACK-TEST-001", packages)
            self.assertEqual(exported["package_count"], 25)
            package = json.loads(
                next(packages.glob("CH5.1.1-*.json")).read_text(encoding="utf-8")
            )
            self.assertGreaterEqual(len(package["outline_nodes"]), 3)
            self.assertIn("heading_path_json", next(
                source["data"] for source in package["sources"]
                if source["source_type"] == "corpus"
            ))


if __name__ == "__main__":
    unittest.main()
