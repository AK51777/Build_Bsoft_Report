from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from benchmark_report_quality import evaluate, template_skeleton  # noqa: E402
from build_dynamic_construction_outline import presentation_title  # noqa: E402
from build_evidence_bound_initial_drafts import (  # noqa: E402
    CHAPTER_ARGUMENT_OUTLINES,
    ensure_minimum_length,
    feature_control_profile,
    generic_section,
    policy_section,
)
from validate_section_draft import visible_length  # noqa: E402


class EvidenceBoundDraftQualityTests(unittest.TestCase):
    def test_minimum_length_guard_uses_validator_visible_length(self) -> None:
        lines = ensure_minimum_length(
            ["## 项目基本情况", "", "已有项目事实。", ""],
            {"official_name": "测试医院信息化建设项目"},
            {
                "chapter_code": "1.1.1",
                "section_title": "项目基本情况",
                "section_role": "project_overview",
                "length_min": 500,
            },
            [{"standard_name": "门诊业务系统"}, {"standard_name": "数据治理平台"}],
        )
        self.assertGreaterEqual(visible_length("\n".join(lines)), 580)

    def test_generic_chapter_has_project_specific_structure_without_template_residue(self) -> None:
        project = {"official_name": "测试医院信息化建设项目"}
        plan = {
            "chapter_code": "6.1.1",
            "section_title": "项目组织与进度安排",
            "section_role": "implementation_operation",
        }
        scopes = [
            {"standard_name": "电子病历系统", "construction_mode": "upgrade"},
            {"standard_name": "医院信息集成平台", "construction_mode": "new_build"},
        ]
        content = "\n".join(generic_section(project, plan, scopes, []))
        self.assertIn("项目治理与组织分工", content)
        self.assertIn("阶段计划与成果物", content)
        for marker in (
            "项目数据库",
            "公司能力",
            "标准方案可复用的机制包括",
            "管理与实施机制",
            "当前登记的",
            "本节结合",
            "可评审初稿",
            "工作稿按",
            "候选功能",
        ):
            self.assertNotIn(marker, content)

    def test_major_chapters_use_distinct_argument_outlines(self) -> None:
        self.assertNotEqual(
            CHAPTER_ARGUMENT_OUTLINES["6.1.1"],
            CHAPTER_ARGUMENT_OUTLINES["6.1.2"],
        )
        self.assertNotEqual(
            CHAPTER_ARGUMENT_OUTLINES["7.1.2"],
            CHAPTER_ARGUMENT_OUTLINES["8.1.1"],
        )

    def test_feature_profiles_prioritize_business_semantics(self) -> None:
        nursing_design, _ = feature_control_profile("护士临床路径", "医嘱执行与交接")
        reminder_design, _ = feature_control_profile("业务提醒", "接口消息触发提醒")
        self.assertIn("患者身份", nursing_design)
        self.assertNotIn("开立、审核", nursing_design)
        self.assertIn("规则来源", reminder_design)
        self.assertNotIn("数据提供方", reminder_design)

    def test_policy_and_standard_material_are_split_without_internal_tags(self) -> None:
        project = {"official_name": "测试医院信息化建设项目"}
        material = {
            "basis_entries": [
                {
                    "policy_id": "POLICY-1",
                    "policy_type": "policy",
                    "title": "公立医院高质量发展意见",
                    "document_no": "国办发〔2021〕18号",
                    "issuer": "国务院办公厅",
                    "publish_date": "2021-06-04",
                    "delivery_eligible": False,
                },
                {
                    "policy_id": "STANDARD-1",
                    "policy_type": "evaluation_rule",
                    "title": "电子病历系统应用水平分级评价标准",
                    "document_no": "国卫办医函〔2018〕1079号",
                    "issuer": "国家卫生健康委办公厅",
                    "publish_date": "2018-12-03",
                    "delivery_eligible": False,
                },
            ],
            "background_paragraphs": [
                {
                    "policy_id": "POLICY-1",
                    "title": "公立医院高质量发展意见",
                    "text": "该文件用于说明项目建设方向和公共价值。",
                    "topic_tags": ["high_quality_hospital"],
                },
                {
                    "policy_id": "STANDARD-1",
                    "title": "电子病历系统应用水平分级评价标准",
                    "text": "该文件用于明确电子病历评价对象和取证要求。",
                    "topic_tags": ["electronic_medical_record", "evaluation"],
                },
            ],
            "catalog_candidates": [
                {
                    "basis_group": "policy",
                    "title": "《十四五全民健康信息化规划》",
                    "document_no": "国卫规划发〔2022〕30号",
                    "issuer": "国家卫生健康委",
                    "publish_date": "2022-11-09",
                },
                {
                    "basis_group": "standard",
                    "title": "《医院智慧服务分级评估标准体系》",
                    "document_no": "国卫办医函〔2019〕236号",
                    "issuer": "国家卫生健康委办公厅",
                    "publish_date": "2019-03-18",
                },
            ],
        }
        policy = "\n".join(policy_section(
            project,
            {"chapter_code": "1.2.1", "section_title": "政策法规依据"},
            material,
            [],
        ))
        standard = "\n".join(policy_section(
            project,
            {"chapter_code": "1.2.2", "section_title": "标准规范依据"},
            material,
            [],
        ))
        self.assertIn("公立医院高质量发展意见", policy)
        self.assertNotIn("电子病历系统应用水平分级评价标准", policy)
        self.assertIn("电子病历系统应用水平分级评价标准", standard)
        self.assertNotIn("公立医院高质量发展意见", standard)
        self.assertIn("十四五全民健康信息化规划", policy)
        self.assertNotIn("医院智慧服务分级评估标准体系", policy)
        self.assertIn("医院智慧服务分级评估标准体系", standard)
        self.assertIn("不得据标题扩写政策要求", policy + standard)
        for tag in ("high_quality_hospital", "electronic_medical_record", "evaluation"):
            self.assertNotIn(tag, policy + standard)

    def test_public_outline_titles_remove_company_branding(self) -> None:
        title = presentation_title("创业慧康 BsoftGPT 医院信息平台")
        self.assertNotIn("创业慧康", title)
        self.assertNotIn("Bsoft", title)
        self.assertIn("大模型智能", title)

    def test_benchmark_blocks_template_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.docx"
            document = Document()
            document.add_heading("第1章 总论", level=1)
            document.add_paragraph("项目数据库中的范围记录用于形成正文。" * 8)
            document.add_paragraph("本项目围绕临床业务、数据治理和运行保障形成完整论证。" * 8)
            document.add_paragraph("各项建设内容均应建立设计、测试、验收和持续改进证据。" * 8)
            document.save(path)
            result = evaluate(path)
        self.assertIn(
            "template_prose_residue",
            {item["code"] for item in result["blockers"]},
        )

    def test_template_skeleton_normalizes_topic_and_scope_substitution(self) -> None:
        first = (
            "针对项目组织与进度安排，实施对象包括电子病历、集成平台、数据中心、患者服务平台等20项建设内容，"
            "计划编排时需识别公共依赖。从建设方式看，升级事项包括电子病历、集成平台、数据中心、患者服务平台。"
        )
        second = (
            "针对投资分项估算，实施对象包括PACS、决策支持、数据治理、移动护理等16项建设内容，"
            "计划编排时需识别公共依赖。从建设方式看，新建事项包括PACS、决策支持、数据治理、移动护理。"
        )
        self.assertEqual(template_skeleton(first), template_skeleton(second))


if __name__ == "__main__":
    unittest.main()
