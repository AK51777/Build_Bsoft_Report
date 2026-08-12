from __future__ import annotations

import json
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


class SectionTaskPackageTests(unittest.TestCase):
    def test_exports_self_contained_json_and_markdown_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project",
                project_code="TEST-PACKAGE-001",
                official_name="Package Test Project",
            )
            database = Path(initialized["database"])
            build_composition_plan(database, "TEST-PACKAGE-001")
            output_dir = root / "packages"

            result = export_packages(
                database, "TEST-PACKAGE-001", output_dir, version_no=1
            )

            self.assertEqual(result["package_count"], 28)
            self.assertEqual(len(list(output_dir.glob("*.json"))), 28)
            self.assertEqual(len(list(output_dir.glob("*.md"))), 28)
            first_json = json.loads(next(output_dir.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(first_json["project"]["project_code"], "TEST-PACKAGE-001")
            self.assertIn("conclusion_boundary", first_json["plan"])
            first_markdown = next(output_dir.glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("## 来源绑定", first_markdown)
            self.assertIn("`prohibited`", first_markdown)
            self.assertIn("不得从产品能力", first_markdown)


if __name__ == "__main__":
    unittest.main()
