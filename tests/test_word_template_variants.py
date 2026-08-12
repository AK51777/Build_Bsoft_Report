from __future__ import annotations

import base64
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_report_docx import build_docx  # noqa: E402


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)


class WordTemplateVariantTests(unittest.TestCase):
    def make_template(self, path: Path, font: str, margin_cm: float, marker: str) -> None:
        document = Document()
        normal = document.styles["Normal"]
        normal.font.name = "Arial"
        normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font)
        section = document.sections[0]
        section.left_margin = Cm(margin_cm)
        section.right_margin = Cm(margin_cm)
        document.add_paragraph(f"OLD-BODY-{marker}")
        document.add_picture(BytesIO(PNG_1X1))
        section.header.paragraphs[0].text = f"OLD-HEADER-{marker}"
        section.footer.paragraphs[0].text = f"OLD-FOOTER-{marker}"
        document.core_properties.author = f"OLD-AUTHOR-{marker}"
        document.save(path)

    def test_three_template_variants_preserve_styles_and_scrub_content(self) -> None:
        variants = [
            ("宋体", 2.0, "A"),
            ("仿宋", 2.5, "B"),
            ("楷体", 3.0, "C"),
        ]
        markdown = "# 模板测试项目\n\n## 可行性研究报告\n\n# 第1章 总论\n\n## 1.1 项目概况\n\n### 1.1.1 项目基本情况\n\n模板回归正文。\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for font, margin_cm, marker in variants:
                template = root / f"template-{marker}.docx"
                output = root / f"output-{marker}.docx"
                self.make_template(template, font, margin_cm, marker)

                result = build_docx(
                    markdown,
                    output,
                    "模板测试项目",
                    "测试单位",
                    template=template,
                )

                self.assertEqual(result["template"], str(template.resolve()))
                generated = Document(output)
                self.assertAlmostEqual(generated.sections[0].left_margin.cm, margin_cm, places=1)
                east_asia = generated.styles["Normal"].element.rPr.rFonts.get(
                    qn("w:eastAsia")
                )
                self.assertEqual(east_asia, font)
                self.assertEqual(generated.core_properties.author, "")
                with zipfile.ZipFile(output) as package:
                    xml_text = "\n".join(
                        package.read(name).decode("utf-8", errors="ignore")
                        for name in package.namelist()
                        if name.endswith((".xml", ".rels"))
                    )
                    self.assertNotIn(f"OLD-BODY-{marker}", xml_text)
                    self.assertNotIn(f"OLD-HEADER-{marker}", xml_text)
                    self.assertNotIn(f"OLD-FOOTER-{marker}", xml_text)
                    self.assertNotIn(f"OLD-AUTHOR-{marker}", xml_text)
                    self.assertFalse(
                        any(name.startswith("word/media/") for name in package.namelist())
                    )


if __name__ == "__main__":
    unittest.main()
