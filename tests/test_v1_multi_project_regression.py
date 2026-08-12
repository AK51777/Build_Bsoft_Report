from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_project_pipeline import run_pipeline  # noqa: E402


class V1MultiProjectRegressionTests(unittest.TestCase):
    def test_three_project_material_structures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            text_project = root / "text-project"
            text_sources = text_project / "原始资料"
            text_sources.mkdir(parents=True)
            (text_sources / "baseline.md").write_text(
                "# 项目概况\n\n这是脱敏测试材料，不代表真实医院事实。\n",
                encoding="utf-8",
            )
            text_first = run_pipeline(
                text_project,
                project_code="REG-TEXT-001",
                official_name="文本材料测试项目",
            )
            text_second = run_pipeline(text_project, project_code="REG-TEXT-001")
            self.assertEqual(len(text_first["processed"]["clean_documents"]), 1)
            self.assertEqual(len(text_second["processed"]["clean_documents"]), 1)
            self.assertEqual(text_first["composition_plan"]["plan_count"], 28)
            self.assertEqual(len(text_second["stage_results"]), 10)
            self.assertEqual(
                text_first["policy"]["match_run_id"], text_second["policy"]["match_run_id"]
            )
            self.assertEqual(
                text_first["document_standard"]["match_run_id"],
                text_second["document_standard"]["match_run_id"],
            )

            conn = sqlite3.connect(text_second["database"])
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM corpus_document").fetchone()[0], 1)
            finally:
                conn.close()

            scope_project = root / "scope-project"
            scope_sources = scope_project / "原始资料"
            scope_sources.mkdir(parents=True)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "建设清单"
            sheet.append(["序号", "建设内容", "分类", "建设方式", "数量", "单位"])
            sheet.append([1, "电子病历系统", "临床", "升级", 1, "套"])
            sheet.append([2, "集成平台", "平台", "优化", None, None])
            workbook.save(scope_sources / "scope.xlsx")
            scope_result = run_pipeline(
                scope_project,
                project_code="REG-SCOPE-001",
                official_name="清单材料测试项目",
            )
            self.assertEqual(len(scope_result["processed"]["scope_workbooks"]), 1)
            conn = sqlite3.connect(scope_result["database"])
            try:
                modes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT construction_mode FROM project_scope_item"
                    )
                }
                self.assertEqual(modes, {"upgrade", "pending_confirmation"})
            finally:
                conn.close()

            blocked_project = root / "blocked-project"
            blocked_sources = blocked_project / "原始资料"
            blocked_sources.mkdir(parents=True)
            (blocked_sources / "scan.pdf").write_bytes(b"%PDF-1.4\n% synthetic placeholder")
            blocked_result = run_pipeline(
                blocked_project,
                project_code="REG-PDF-001",
                official_name="PDF阻断测试项目",
            )
            self.assertEqual(blocked_result["status"], "blocked")
            self.assertEqual(blocked_result["blockers"][0]["reason"], "unsupported_or_ocr_required")
            self.assertTrue(
                (blocked_project / "运行记录" / "pipeline-result.json").is_file()
            )

            for project in (text_project, scope_project, blocked_project):
                pipeline_result = json.loads(
                    (project / "运行记录" / "pipeline-result.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(pipeline_result["status"], "blocked")
                self.assertEqual(len(pipeline_result["stage_results"]), 10)
                self.assertTrue((project / "11-正文工作稿" / "report-working.md").is_file())
                self.assertTrue((project / "数据包" / "结构化数据" / "source-inventory.json").is_file())
                self.assertTrue((project / "数据包" / "结构化数据" / "scope-baseline.json").is_file())
                self.assertTrue((project / "数据包" / "结构化数据" / "traceability-matrix.csv").is_file())
                self.assertTrue((project / "15-项目复盘.md").is_file())


if __name__ == "__main__":
    unittest.main()
