from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble_report_markdown import assemble  # noqa: E402
from build_report_docx import build_docx  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from manage_section_draft import manage_draft  # noqa: E402
from save_section_draft import save_draft  # noqa: E402
from validate_full_report import validate_report  # noqa: E402


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
            manage_draft(database, "TEST-DELIVERY-001", "1.1.1", 1, "adopt")

            assembled = assemble(database, "TEST-DELIVERY-001", "delivery")
            self.assertEqual(assembled["missing_adopted_sections"], [])
            self.assertIn("# 第1章 总论", assembled["content"])
            validation = validate_report(database, "TEST-DELIVERY-001", mode="delivery")
            self.assertEqual(validation["status"], "passed")

            output = root / "report.docx"
            build_docx(
                assembled["content"], output, "测试医院信息化项目", "测试医院"
            )
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as package_zip:
                document_xml = package_zip.read("word/document.xml").decode("utf-8")
                settings_xml = package_zip.read("word/settings.xml").decode("utf-8")
                self.assertIn('TOC \\o "1-3"', document_xml)
                self.assertIn('w:val="Heading1"', document_xml)
                self.assertIn("w:updateFields", settings_xml)

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM validation_run").fetchone()[0], 1)
            finally:
                conn.close()

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


if __name__ == "__main__":
    unittest.main()
