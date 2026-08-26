from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_reference_corpus_workpack import build_workpack  # noqa: E402
from build_reference_corpus_review_pack import (  # noqa: E402
    recommended_decision,
    review_findings,
)
from build_section_composition_plan import (  # noqa: E402
    assessment_targets_compatible,
    narrative_block_eligible,
)
from extract_clean_document_blocks import build_payload, docx_blocks  # noqa: E402
from ingest_clean_documents_sqlite import ingest_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


TAXONOMY = json.loads(
    (
        SKILL_ROOT
        / "assets"
        / "knowledge-base"
        / "seeds"
        / "reference_corpus_taxonomy_v1.json"
    ).read_text(encoding="utf-8")
)


class ReferenceCorpusWorkflowTests(unittest.TestCase):
    def test_review_recommendation_blocks_fixed_technology_and_undeclared_variables(self) -> None:
        block = {
            "clean_text": "采用RPC协议和ESB技术，并符合{{city_name}}有关要求。",
            "variable_slots": [],
            "content_type": "feasibility_narrative",
            "reuse_class": "B",
            "review_flags": [],
        }
        findings = review_findings(block)
        self.assertEqual(recommended_decision(block, findings), "prohibited")
        self.assertIn("fixed_technology_anchor", {item["code"] for item in findings})
        self.assertIn("undeclared_variable", {item["code"] for item in findings})

    def test_narrative_retrieval_does_not_mix_standard_solution_or_legacy_blocks(self) -> None:
        base = {
            "source_corpus_type": "reference_feasibility",
            "content_type": "feasibility_narrative",
            "applicable_project_types_json": '["smart_hospital"]',
            "corpus_project_type": "smart_hospital",
        }
        self.assertTrue(narrative_block_eligible(base, "smart_hospital"))
        self.assertTrue(
            narrative_block_eligible(base, "hospital_informationization")
        )
        self.assertFalse(
            narrative_block_eligible(
                {**base, "source_corpus_type": "standard_solution"},
                "smart_hospital",
            )
        )
        self.assertFalse(
            narrative_block_eligible(
                {**base, "source_corpus_type": "legacy_unspecified"},
                "smart_hospital",
            )
        )
        self.assertFalse(narrative_block_eligible(base, "medical_consortium"))

    def test_assessment_specific_block_requires_matching_project_target(self) -> None:
        block = {
            "assessment_targets_json": json.dumps(
                [
                    {"framework": "emr", "target": "五级"},
                    {"framework": "interoperability", "target": "四级甲等"},
                ],
                ensure_ascii=False,
            )
        }
        self.assertTrue(
            assessment_targets_compatible(
                block, {"emr": {"5"}, "interoperability": {"4a"}}
            )
        )
        self.assertFalse(
            assessment_targets_compatible(
                block, {"emr": {"6"}, "interoperability": {"4a"}}
            )
        )
        self.assertFalse(assessment_targets_compatible(block, {}))

    def test_numeric_word_style_ids_resolve_to_heading_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "numeric-style.docx"
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="3"/></w:pPr><w:r><w:t>项目总体设计</w:t></w:r></w:p>
<w:p><w:r><w:t>正文内容。</w:t></w:r></w:p>
</w:body></w:document>"""
            styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="3"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
</w:styles>"""
            with zipfile.ZipFile(docx, "w") as package:
                package.writestr("word/document.xml", document_xml)
                package.writestr("word/styles.xml", styles_xml)

            blocks = docx_blocks(docx)

            self.assertEqual(blocks[0]["block_type"], "heading")
            self.assertEqual(blocks[0]["heading_level"], 1)
            self.assertEqual(blocks[1]["heading_path"], ["项目总体设计"])

    def test_builds_review_gated_two_axis_workpack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "reference.md"
            source.write_text(
                "# 项目总体设计\n\n"
                "## 项目建设目标、规模与内容\n\n"
                "### 整体目标\n\n"
                "示例市人民医院围绕电子病历五级和互联互通四级甲等目标完善整体能力。\n\n"
                "## 应用软件建设方案\n\n"
                "### HIS系统\n\n"
                "建设门诊业务功能和住院业务功能。\n",
                encoding="utf-8",
            )
            clean_payload = build_payload(
                source, "TEST-REFERENCE-001", "Reference", True
            )

            workpack = build_workpack(
                clean_payload,
                TAXONOMY,
                source_corpus_type="reference_feasibility",
                project_type="smart_hospital",
                variables=[("hospital_name", "示例市人民医院")],
            )

            self.assertEqual(workpack["review_decision"]["status"], "pending")
            self.assertTrue(workpack["safeguards"]["human_review_required_before_publish"])
            self.assertEqual(len(workpack["blocks"]), 1)
            block = workpack["blocks"][0]
            self.assertEqual(block["semantic_section"], "overall_objective_scope")
            self.assertEqual(block["content_slot"], "overall_objective")
            self.assertEqual(block["content_type"], "feasibility_narrative")
            self.assertEqual(block["source_corpus_type"], "reference_feasibility")
            self.assertIn("{{hospital_name}}", block["clean_text"])
            self.assertFalse(block["publish_eligible"])
            self.assertEqual(
                workpack["summary"]["excluded_group_counts"]["construction_solution"],
                1,
            )

    def test_ingests_semantic_fields_without_auto_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-REFERENCE-DB"
            )
            source = root / "reference.md"
            source.write_text(
                "# 项目建设效益\n\n## 社会效益\n\n通过业务协同和数据共享提升医疗服务效率、质量与患者就医体验。\n",
                encoding="utf-8",
            )
            clean_payload = build_payload(
                source, "TEST-REFERENCE-DB", "Reference", False
            )
            workpack = build_workpack(
                clean_payload,
                TAXONOMY,
                source_corpus_type="reference_feasibility",
                project_type="smart_hospital",
                variables=[],
            )

            ingest_payload(Path(initialized["database"]), workpack)

            with closing(sqlite3.connect(initialized["database"])) as connection:
                document = connection.execute(
                    "SELECT source_corpus_type,project_type,review_status FROM corpus_document"
                ).fetchone()
                block = connection.execute(
                    "SELECT content_type,semantic_section,content_slot,adaptation_mode,review_status FROM corpus_block"
                ).fetchone()
            self.assertEqual(document, ("reference_feasibility", "smart_hospital", "pending"))
            self.assertEqual(
                block,
                (
                    "feasibility_narrative",
                    "construction_benefit",
                    "social_benefit",
                    "parameterized",
                    "pending",
                ),
            )


if __name__ == "__main__":
    unittest.main()
