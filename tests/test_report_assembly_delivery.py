from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble_report_markdown import assemble  # noqa: E402
from audit_delivery_artifact import audit as audit_delivery_artifact  # noqa: E402
from build_report_docx import build_docx  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from manage_section_draft import manage_draft  # noqa: E402
from save_section_draft import save_draft  # noqa: E402
from validate_full_report import validate_report  # noqa: E402
from validate_section_draft import validate_draft  # noqa: E402


BLUEPRINTS = {
    "document_type": "feasibility_study",
    "blueprints": [
        {
            "section_role": "test_role",
            "purpose": "Test purpose",
            "required_questions": [],
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
            "section_title": "项目基本情况",
            "section_role": "test_role",
        }
    ],
}


class ReportAssemblyDeliveryTests(unittest.TestCase):
    def test_adopted_report_validates_and_builds_structural_docx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project",
                project_code="TEST-DELIVERY-001",
                official_name="测试医院信息化项目",
                owner_name="测试医院",
            )
            database = Path(initialized["database"])
            build_composition_plan(
                database, "TEST-DELIVERY-001", blueprint_payload=BLUEPRINTS
            )
            packages = root / "packages"
            export_packages(database, "TEST-DELIVERY-001", packages)
            package = json.loads(next(packages.glob("*.json")).read_text(encoding="utf-8"))
            save_draft(
                database,
                "TEST-DELIVERY-001",
                "1.1.1",
                "测试医院信息化项目依据已确认材料形成当前工作稿。",
                package,
                provider="openai",
                model="test-model",
                prompt_version="v1",
            )
            validate_draft(database, "TEST-DELIVERY-001", "1.1.1", 1)
            manage_draft(database, "TEST-DELIVERY-001", "1.1.1", 1, "adopt")

            assembled = assemble(database, "TEST-DELIVERY-001", "delivery")
            self.assertEqual(assembled["missing_adopted_sections"], [])
            self.assertIn("# 第1章 总论", assembled["content"])
            validation = validate_report(database, "TEST-DELIVERY-001", mode="delivery")
            self.assertEqual(validation["status"], "passed")

            output = root / "report.docx"
            working = build_docx(
                assembled["content"], output, "测试医院信息化项目", "测试医院"
            )
            self.assertEqual(working["mode"], "working")
            self.assertEqual(working["markdown_residue"], {
                "heading_markers": 0, "bold_markers": 0, "code_markers": 0
            })
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as package_zip:
                document_xml = package_zip.read("word/document.xml").decode("utf-8")
                settings_xml = package_zip.read("word/settings.xml").decode("utf-8")
                self.assertIn('TOC \\o "1-3"', document_xml)
                self.assertIn('w:val="Heading1"', document_xml)
                self.assertIn("华文中宋", document_xml)
                self.assertNotIn("方正小标宋简体", document_xml)
                self.assertIn("w:updateFields", settings_xml)
                self.assertIn("工作稿（未通过正式交付门禁）", document_xml)

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM validation_run").fetchone()[0], 1)
            finally:
                conn.close()

    def test_formal_delivery_cannot_bypass_database_or_template(self) -> None:
        markdown = "# 项目\n\n# 第1章 总论\n\n#### 1.1.1 标题\n\n**正文**\n"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "delivery.docx"
            with self.assertRaises(RuntimeError):
                build_docx(markdown, output, "项目", mode="delivery")
            self.assertFalse(output.exists())

    def test_heading_four_and_seven_and_inline_markdown_are_semantic_word_content(self) -> None:
        markdown = "# 项目\n\n# 第1章 总论\n\n#### 1.1.1 四级标题\n\n####### 1.1.1.1.1.1.1 七级标题\n\n正文含**加粗**、*斜体*和`代码`。\n"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "working.docx"
            result = build_docx(markdown, output, "项目")
            self.assertEqual(result["heading_levels"]["4"], 1)
            self.assertEqual(result["heading_levels"]["7"], 1)
            self.assertFalse(any(result["markdown_residue"].values()))
            with zipfile.ZipFile(output) as package_zip:
                document_xml = package_zip.read("word/document.xml").decode("utf-8")
                self.assertIn('w:val="Heading4"', document_xml)
                self.assertIn('w:val="Heading7"', document_xml)
                self.assertNotIn("####", document_xml)
                self.assertNotIn("**", document_xml)

    def test_markdown_table_repeats_header_across_pages(self) -> None:
        markdown = (
            "# 项目\n\n# 第1章 总论\n\n"
            "| 列一 | 列二 |\n| --- | --- |\n| 内容 | 内容 |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "table.docx"
            build_docx(markdown, output, "测试项目")
            with zipfile.ZipFile(output) as package_zip:
                document_xml = package_zip.read("word/document.xml").decode("utf-8")
            self.assertIn("w:tblHeader", document_xml)

    def test_delivery_assembly_blocks_missing_adopted_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="TEST-DELIVERY-BLOCK"
            )
            database = Path(initialized["database"])
            build_composition_plan(
                database, "TEST-DELIVERY-BLOCK", blueprint_payload=BLUEPRINTS
            )
            with self.assertRaises(RuntimeError):
                assemble(database, "TEST-DELIVERY-BLOCK", "delivery")

    def test_delivery_validation_is_hash_bound_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-DELIVERY-HASH",
                official_name="哈希门禁测试项目", owner_name="测试医院",
            )
            database = Path(initialized["database"])
            build_composition_plan(
                database, "TEST-DELIVERY-HASH", blueprint_payload=BLUEPRINTS
            )
            packages = root / "packages"
            export_packages(database, "TEST-DELIVERY-HASH", packages)
            package = json.loads(next(packages.glob("*.json")).read_text(encoding="utf-8"))
            save_draft(
                database, "TEST-DELIVERY-HASH", "1.1.1",
                "哈希门禁测试项目依据已确认材料形成正文。", package,
                provider="openai", model="test-model", prompt_version="v1",
            )
            validate_draft(database, "TEST-DELIVERY-HASH", "1.1.1", 1)
            manage_draft(database, "TEST-DELIVERY-HASH", "1.1.1", 1, "adopt")
            validation = validate_report(database, "TEST-DELIVERY-HASH", mode="delivery")
            self.assertEqual(validation["status"], "passed")
            assembled = assemble(database, "TEST-DELIVERY-HASH", "delivery")
            template = root / "template.docx"
            Document().save(template)
            result = build_docx(
                assembled["content"], root / "delivery.docx", "哈希门禁测试项目",
                "测试医院", template, mode="delivery", database=database,
                project_code="TEST-DELIVERY-HASH",
            )
            self.assertEqual(result["authorization"]["validation_run_id"], validation["validation_run_id"])
            summary = root / "build-summary.json"
            summary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            artifact_audit = audit_delivery_artifact(
                root / "delivery.docx", build_summary=summary,
                database=database, project_code="TEST-DELIVERY-HASH",
            )
            self.assertEqual(artifact_audit["status"], "passed")
            replacement = root / "replacement.docx"
            Document().save(replacement)
            (root / "delivery.docx").write_bytes(replacement.read_bytes())
            replaced_audit = audit_delivery_artifact(
                root / "delivery.docx", build_summary=summary,
                database=database, project_code="TEST-DELIVERY-HASH",
            )
            self.assertEqual(replaced_audit["status"], "failed")
            self.assertIn(
                "summary_docx_hash_mismatch",
                {item["code"] for item in replaced_audit["blockers"]},
            )

            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    "UPDATE draft_section_version SET content=content || ' 篡改后的新增正文。' WHERE status='adopted'"
                )
                conn.commit()
            finally:
                conn.close()
            tampered = assemble(database, "TEST-DELIVERY-HASH", "delivery")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                build_docx(
                    tampered["content"], root / "tampered.docx", "哈希门禁测试项目",
                    "测试医院", template, mode="delivery", database=database,
                    project_code="TEST-DELIVERY-HASH",
                )
            self.assertFalse((root / "tampered.docx").exists())


if __name__ == "__main__":
    unittest.main()
