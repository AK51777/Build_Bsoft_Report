from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_evidence_bound_initial_drafts import build_initial_drafts  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from chapter_rules import regression_targets, resolve_chapter_rule  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from validate_section_draft import assess_content  # noqa: E402


SINGLE_CHAPTER_BLUEPRINT = {
    "document_type": "feasibility_study",
    "blueprints": [
        {
            "section_role": "test_role",
            "purpose": "验证单章增量链路。",
            "required_questions": [],
            "required_fact_categories": [],
            "required_scope_types": [],
            "required_policy_topics": [],
            "required_tables": [],
            "forbidden_content": [],
            "completion_rules": [],
        }
    ],
    "outline": [
        {
            "chapter_code": "1.1.1",
            "section_title": "项目基本情况",
            "section_role": "test_role",
        },
        {
            "chapter_code": "1.1.2",
            "section_title": "建设范围与目标",
            "section_role": "test_role",
        },
    ],
}


class ChapterRuleLayerTests(unittest.TestCase):
    def test_511_contract_merges_global_role_and_chapter_layers(self) -> None:
        contract = resolve_chapter_rule("5.1.1", "construction_content")
        self.assertEqual(
            contract["rule_layers"],
            ["global", "role:construction_content", "chapter:5.1.1"],
        )
        self.assertEqual(contract["assembly_mode"], "full_confirmed_standard_solution")
        self.assertEqual(
            contract["required_source_types"], ["scope", "capability", "corpus"]
        )
        self.assertTrue(contract["validation"]["require_standard_block_markers"])
        self.assertTrue(contract["validation"]["require_standard_block_text"])

    def test_regression_tiers_are_bounded(self) -> None:
        chapter = regression_targets("5.1.1", "chapter")
        family = regression_targets("5.1.1", "family")
        self.assertIn("tests.test_chapter_incremental_workflow", chapter)
        self.assertGreater(len(family), len(chapter))

    def test_current_state_contract_separates_required_and_optional_facts(self) -> None:
        contract = resolve_chapter_rule("2.1.2", "current_state")
        self.assertEqual(contract["assembly_mode"], "current_state_evidence_synthesis")
        self.assertEqual(contract["required_fact_categories"], ["current_state"])
        self.assertIn("acceptance", contract["optional_fact_categories"])
        self.assertIn("business.accreditation", contract["optional_fact_categories"])
        self.assertTrue(
            contract["validation"]["require_current_state_fact_traceability"]
        )
        self.assertIn(
            "tests.test_current_state_chapter",
            regression_targets("2.1.2", "chapter"),
        )

    def test_policy_evidence_contract_has_four_basis_groups_and_three_background_levels(self) -> None:
        basis = resolve_chapter_rule("1.2.1", "basis")
        background = resolve_chapter_rule("2.1.1", "policy_background")
        self.assertEqual(basis["assembly_mode"], "four_group_verified_basis")
        self.assertEqual(
            basis["argument_outline"],
            ["政策类依据", "行业标准依据", "安全类标准依据", "投资估算编制依据"],
        )
        self.assertEqual(background["assembly_mode"], "jurisdiction_policy_background")
        self.assertEqual(
            background["argument_outline"],
            ["国家政策背景", "省/自治区政策背景", "市/项目建设地区政策背景"],
        )
        self.assertTrue(
            background["validation"]["require_ordered_policy_basis_subsequence"]
        )
        blueprint = json.loads(
            (SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "section_blueprints_v1.json").read_text(
                encoding="utf-8"
            )
        )
        outline = {row["chapter_code"]: row for row in blueprint["outline"]}
        self.assertEqual(outline["1.2.1"]["section_title"], "可行性研究报告编制依据")
        self.assertEqual(outline["1.2.2"]["section_title"], "编制依据适用与动态更新")
        self.assertEqual(outline["2.1.1"]["section_role"], "policy_background")


class SingleChapterPipelineTests(unittest.TestCase):
    def test_plan_package_and_draft_can_be_built_for_one_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project",
                project_code="CHAPTER-ONLY-001",
                official_name="单章增量测试项目",
            )
            database = Path(initialized["database"])
            plan = build_composition_plan(
                database,
                "CHAPTER-ONLY-001",
                blueprint_payload=SINGLE_CHAPTER_BLUEPRINT,
                chapter_codes=["1.1.1"],
            )
            self.assertEqual(plan["selection_mode"], "chapter")
            self.assertEqual(plan["plan_count"], 1)
            package_dir = root / "packages"
            packages = export_packages(
                database,
                "CHAPTER-ONLY-001",
                package_dir,
                chapter_codes=["1.1.1"],
            )
            self.assertEqual(packages["package_count"], 1)
            package_path = next(package_dir.glob("*.json"))
            package = json.loads(package_path.read_text(encoding="utf-8"))
            self.assertEqual(package["schema_version"], "1.1")
            self.assertEqual(package["generation_contract"]["chapter_code"], "1.1.1")
            result = build_initial_drafts(
                database,
                "CHAPTER-ONLY-001",
                package_dir,
                root / "drafts",
                chapter_codes=["1.1.1"],
            )
            self.assertEqual(result["selection_mode"], "chapter")
            self.assertEqual(result["section_count"], 1)
            self.assertEqual(result["sections"][0]["chapter_code"], "1.1.1")


class ChapterContractValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "chapter_code": "5.1.1",
            "section_role": "construction_content",
            "length_min": 0,
            "required_tables_json": "[]",
            "outline_nodes": [],
        }
        self.source_text = "标准方案正文围绕规则配置、业务处理、异常反馈和结果追踪形成完整闭环。"
        self.sources = [
            {
                "source_type": "corpus",
                "source_object_id": "BLOCK-001",
                "usage_mode": "parameterized",
                "clean_text": self.source_text,
                "forbidden_terms_json": "[]",
            }
        ]

    def test_511_blocks_missing_marker_or_full_text(self) -> None:
        result = assess_content("只保留摘要。", self.plan, self.sources, mode="working")
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("standard_solution_block_marker_missing", codes)
        self.assertIn("standard_solution_block_text_missing", codes)

    def test_511_accepts_traced_full_standard_block(self) -> None:
        content = f"<!-- standard-blocks: BLOCK-001 -->\n\n{self.source_text}"
        result = assess_content(content, self.plan, self.sources, mode="working")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["assembly_mode"], "full_confirmed_standard_solution")

    def test_authoring_process_language_is_blocked(self) -> None:
        content = "正式报审前还需复核文件有效性。"
        result = assess_content(content, self.plan, [], mode="working")
        self.assertIn(
            "authoring_process_language", {item["code"] for item in result["issues"]}
        )

    def test_scope_full_carry_is_limited_to_scope_coverage_chapters(self) -> None:
        source = {
            "source_type": "scope",
            "source_object_id": "SCOPE-001",
            "usage_mode": "direct",
            "standard_name": "门诊诊间结算系统",
        }
        objective_plan = {
            "chapter_code": "4.1.2",
            "section_role": "overall_design",
            "length_min": 0,
            "required_tables_json": "[]",
            "outline_nodes": [],
        }
        objective_result = assess_content(
            "本项目围绕医院业务协同和平台数据能力形成总体目标。",
            objective_plan,
            [source],
            mode="working",
        )
        self.assertNotIn(
            "scope_not_carried", {item["code"] for item in objective_result["issues"]}
        )

        construction_result = assess_content(
            "本节承载客户确认的应用软件建设内容。",
            self.plan,
            [source],
            mode="working",
        )
        self.assertIn(
            "scope_not_carried", {item["code"] for item in construction_result["issues"]}
        )


if __name__ == "__main__":
    unittest.main()
