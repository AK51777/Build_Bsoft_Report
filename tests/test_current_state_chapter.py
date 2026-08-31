from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_evidence_bound_initial_drafts import (  # noqa: E402
    build_initial_drafts,
    current_state_section,
)
from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from ingest_project_facts import ingest  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from validate_section_draft import assess_content, validate_draft  # noqa: E402


def fact_source(
    fact_id: str,
    fact_key: str,
    fact_content: str,
    *,
    status: str = "confirmed",
    usage_mode: str = "direct",
) -> dict:
    return {
        "source_type": "fact",
        "source_object_id": fact_id,
        "usage_mode": usage_mode,
        "data": {
            "fact_id": fact_id,
            "fact_key": fact_key,
            "fact_content": fact_content,
            "fact_status": status,
        },
    }


class CurrentStateGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = {
            "official_name": "脱敏测试医院信息化项目",
            "acceptance_targets_json": "[]",
        }
        self.plan = {
            "chapter_code": "2.1.2",
            "section_title": "信息化建设现状",
            "section_role": "current_state",
        }

    def test_combines_rating_baseline_targets_background_and_survey_facts(self) -> None:
        sources = [
            fact_source(
                "FACT-EMR-CURRENT",
                "current_state.assessment.emr.level",
                "电子病历系统应用水平已通过四级评价。",
            ),
            fact_source(
                "FACT-EMR-TARGET",
                "acceptance.emr_level",
                "电子病历系统应用水平目标为五级。",
            ),
            fact_source(
                "FACT-INTEROP-CURRENT",
                "current_state.assessment.interoperability.status",
                "医院尚未参与互联互通标准化成熟度测评。",
            ),
            fact_source(
                "FACT-INTEROP-TARGET",
                "acceptance.interop_level",
                "互联互通标准化成熟度目标为四级甲等。",
            ),
            fact_source(
                "FACT-SERVICE-TARGET",
                "acceptance.smart_service_level",
                "智慧服务目标为三级。",
                status="pending_confirmation",
                usage_mode="prohibited",
            ),
            fact_source(
                "FACT-ACCREDITATION",
                "business.accreditation.tertiary_grade_a",
                "医院正在推进三级甲等医院创建工作。",
            ),
            fact_source(
                "FACT-APPLICATION",
                "current_state.application.emr",
                "现有电子病历系统承担门诊和住院病历书写。",
            ),
            fact_source(
                "FACT-INTERFACE",
                "current_state.interface.monitoring",
                "部分接口异常仍以人工巡查方式发现。",
            ),
            fact_source(
                "FACT-INFRA",
                "current_state.infrastructure.server",
                "现有服务器资源由院内机房承载。",
            ),
            fact_source(
                "FACT-SECURITY",
                "current_state.security.audit",
                "已部署基础日志审计措施。",
            ),
            fact_source(
                "FACT-OPERATION",
                "current_state.operation.monitoring",
                "统一运维监测口径尚待确认。",
                status="pending_confirmation",
                usage_mode="prohibited",
            ),
        ]
        task_package = {
            "generation_contract": {
                "assembly_mode": "current_state_evidence_synthesis"
            },
            "sources": sources,
        }
        scopes = [
            {
                "standard_name": "电子病历系统",
                "construction_mode": "upgrade",
            }
        ]
        content = "\n".join(
            current_state_section(
                self.project, self.plan, scopes, ["现状系统表"], task_package
            )
        )

        self.assertIn("已通过四级评价", content)
        self.assertIn("目标为五级", content)
        self.assertNotIn("已通过五级", content)
        self.assertIn("未参评表示尚无正式外部评级结果，不等于零级", content)
        self.assertIn("等级差异本身只用于确定核查范围", content)
        self.assertIn("创三甲、医院发展规划等背景", content)
        self.assertIn("仅作为现状核查线索", content)
        self.assertIn("<!-- unresolved-fact: FACT-SERVICE-TARGET -->", content)
        self.assertIn("<!-- unresolved-fact: FACT-OPERATION -->", content)
        for dimension in (
            "application",
            "data_interface",
            "infrastructure",
            "security",
            "operation",
        ):
            self.assertIn(f"<!-- current-state-dimension: {dimension} -->", content)
        for fact_id in (
            "FACT-EMR-CURRENT",
            "FACT-EMR-TARGET",
            "FACT-INTEROP-CURRENT",
            "FACT-INTEROP-TARGET",
            "FACT-ACCREDITATION",
            "FACT-APPLICATION",
            "FACT-INTERFACE",
            "FACT-INFRA",
            "FACT-SECURITY",
        ):
            self.assertIn(f"<!-- evidence: {fact_id} -->", content)

    def test_target_without_current_rating_remains_a_baseline_gap(self) -> None:
        task_package = {
            "generation_contract": {
                "assembly_mode": "current_state_evidence_synthesis"
            },
            "sources": [
                fact_source(
                    "FACT-SERVICE-TARGET",
                    "acceptance.smart_service_level",
                    "智慧服务目标为三级。",
                )
            ],
        }
        content = "\n".join(
            current_state_section(
                self.project, self.plan, [], ["现状系统表"], task_package
            )
        )
        self.assertIn("【待补充】当前评级或参评状态", content)
        self.assertIn("已登记规划目标，但当前评级或参评状态尚待补充", content)
        self.assertNotIn("已达到三级", content)

    def test_pending_current_rating_does_not_create_a_false_gap_claim(self) -> None:
        task_package = {
            "generation_contract": {
                "assembly_mode": "current_state_evidence_synthesis"
            },
            "sources": [
                fact_source(
                    "FACT-EMR-CURRENT-PENDING",
                    "current_state.assessment.emr.level",
                    "电子病历系统应用水平当前评级暂无资料。",
                    status="pending_supplement",
                    usage_mode="prohibited",
                ),
                fact_source(
                    "FACT-EMR-TARGET",
                    "acceptance.emr_level",
                    "电子病历系统应用水平目标为五级。",
                ),
            ],
        }
        content = "\n".join(
            current_state_section(
                self.project, self.plan, [], ["现状系统表"], task_package
            )
        )
        self.assertIn("【待补充】电子病历系统应用水平当前评级暂无资料", content)
        self.assertIn("已登记规划目标，但当前评级或参评状态尚待补充", content)
        self.assertNotIn("当前结果与规划目标之间形成对标提升任务", content)


class CurrentStateValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "chapter_code": "2.1.2",
            "section_title": "信息化建设现状",
            "section_role": "current_state",
            "length_min": 0,
            "required_tables_json": "[]",
            "outline_nodes": [],
        }
        self.dimensions = "\n".join(
            f"<!-- current-state-dimension: {item} -->"
            for item in (
                "application",
                "data_interface",
                "infrastructure",
                "security",
                "operation",
            )
        )

    @staticmethod
    def source(
        fact_id: str,
        fact_key: str,
        fact_content: str,
        *,
        usage_mode: str = "direct",
        fact_status: str = "confirmed",
        evidence_count: int = 1,
    ) -> dict:
        return {
            "source_type": "fact",
            "source_object_id": fact_id,
            "usage_mode": usage_mode,
            "fact_key": fact_key,
            "fact_content": fact_content,
            "fact_status": fact_status,
            "evidence_count": evidence_count,
        }

    def test_validates_fact_coverage_dimensions_and_unresolved_disclosure(self) -> None:
        sources = [
            self.source(
                "FACT-CURRENT",
                "current_state.assessment.emr.level",
                "电子病历系统应用水平已通过四级评价。",
            ),
            self.source(
                "FACT-TARGET",
                "acceptance.emr_level",
                "电子病历系统应用水平目标为五级。",
            ),
            self.source(
                "FACT-PENDING",
                "current_state.operation.monitoring",
                "统一监测范围待确认。",
                usage_mode="prohibited",
                fact_status="pending_confirmation",
                evidence_count=0,
            ),
        ]
        content = "\n".join(
            [
                "<!-- evidence: FACT-CURRENT -->",
                "电子病历系统应用水平已通过四级评价。",
                "<!-- evidence: FACT-TARGET -->",
                "电子病历系统应用水平目标为五级。",
                "<!-- unresolved-fact: FACT-PENDING -->",
                "【待确认】统一监测范围待确认。",
                self.dimensions,
            ]
        )
        result = assess_content(content, self.plan, sources, mode="working")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            result["current_state_metrics"]["deterministic_fact_source_coverage"],
            1.0,
        )
        self.assertEqual(
            result["current_state_metrics"]["deterministic_fact_citation_coverage"],
            1.0,
        )

    def test_blocks_missing_evidence_citation_dimension_and_pending_disclosure(self) -> None:
        sources = [
            self.source(
                "FACT-CURRENT",
                "current_state.application.emr",
                "现有电子病历系统正在使用。",
                evidence_count=0,
            ),
            self.source(
                "FACT-PENDING",
                "current_state.operation.monitoring",
                "统一监测范围待确认。",
                usage_mode="prohibited",
                fact_status="conflict",
                evidence_count=0,
            ),
        ]
        result = assess_content(
            "现状说明。\n<!-- current-state-dimension: application -->",
            self.plan,
            sources,
            mode="working",
        )
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("current_state_fact_without_evidence", codes)
        self.assertIn("current_state_fact_not_cited", codes)
        self.assertIn("current_state_dimension_incomplete", codes)
        self.assertIn("current_state_unresolved_fact_not_disclosed", codes)

    def test_blocks_target_as_current_and_not_participated_as_zero(self) -> None:
        sources = [
            self.source(
                "FACT-TARGET",
                "acceptance.smart_service_level",
                "智慧服务目标为三级。",
            ),
            self.source(
                "FACT-INTEROP",
                "current_state.assessment.interoperability.status",
                "医院尚未参与互联互通测评。",
            ),
        ]
        content = "\n".join(
            [
                "<!-- evidence: FACT-TARGET -->",
                "智慧服务已达到三级。",
                "<!-- evidence: FACT-INTEROP -->",
                "医院未参与互联互通测评，现状为零级且不具备相应能力。",
                self.dimensions,
            ]
        )
        result = assess_content(content, self.plan, sources, mode="working")
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("assessment_target_as_current_state", codes)
        self.assertIn("assessment_not_participated_as_zero_or_absence", codes)

    def test_blocks_planned_scope_as_current_without_fact_support(self) -> None:
        sources = [
            {
                "source_type": "scope",
                "source_object_id": "SCOPE-EMR",
                "usage_mode": "survey_lead",
                "standard_name": "电子病历系统",
            }
        ]
        content = "医院现有电子病历系统已上线。\n" + self.dimensions
        result = assess_content(content, self.plan, sources, mode="working")
        self.assertIn(
            "planned_scope_as_current_state",
            {item["code"] for item in result["issues"]},
        )


class CurrentStatePipelineTests(unittest.TestCase):
    def test_database_to_validated_current_state_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_code = "CURRENT-STATE-E2E"
            initialized = initialize_project(
                root / "project",
                project_code=project_code,
                official_name="脱敏测试医院信息化项目",
            )
            database = Path(initialized["database"])
            source_id = "SRC-CURRENT-STATE-SURVEY"
            fact_specs = [
                (
                    "FACT-EMR-CURRENT",
                    "current_state.assessment.emr.level",
                    "current_state",
                    "电子病历系统应用水平已通过四级评价，评级文件已由项目组核验。",
                ),
                (
                    "FACT-EMR-TARGET",
                    "acceptance.emr_level",
                    "acceptance",
                    "本项目电子病历系统应用水平目标为五级。",
                ),
                (
                    "FACT-INTEROP-CURRENT",
                    "current_state.assessment.interoperability.status",
                    "current_state",
                    "医院尚未参与互联互通标准化成熟度测评，当前无正式外部评级结果。",
                ),
                (
                    "FACT-INTEROP-TARGET",
                    "acceptance.interop_level",
                    "acceptance",
                    "本项目互联互通标准化成熟度目标为四级甲等。",
                ),
                (
                    "FACT-ACCREDITATION",
                    "business.accreditation.tertiary_grade_a",
                    "business",
                    "医院正在推进三级甲等医院创建工作，相关管理要求构成本项目建设背景。",
                ),
                (
                    "FACT-APPLICATION",
                    "current_state.application.emr",
                    "current_state",
                    "现有电子病历系统承担门诊、住院病历书写和病历归档，部分质控环节仍需人工复核。",
                ),
                (
                    "FACT-INTERFACE",
                    "current_state.interface.monitoring",
                    "current_state",
                    "现有系统间已形成若干业务接口，接口台账与异常监测口径尚未完全统一。",
                ),
                (
                    "FACT-INFRASTRUCTURE",
                    "current_state.infrastructure.server",
                    "current_state",
                    "现有服务器及存储资源由院内机房承载，资源利用率和扩展余量已纳入本次调研。",
                ),
                (
                    "FACT-SECURITY",
                    "current_state.security.audit",
                    "current_state",
                    "医院已部署基础日志审计与边界防护措施，安全策略覆盖范围仍需按系统清单逐项核验。",
                ),
                (
                    "FACT-OPERATION",
                    "current_state.operation.monitoring",
                    "current_state",
                    "当前运维工作包含日常巡检和故障处置，但统一监控、工单闭环与服务指标尚未形成一致口径。",
                ),
            ]
            ingest(
                database,
                {
                    "project": {
                        "project_code": project_code,
                        "official_name": "脱敏测试医院信息化项目",
                        "document_type": "feasibility_study",
                    },
                    "sources": [
                        {
                            "source_id": source_id,
                            "source_scope": "project",
                            "source_class": "survey_record",
                            "file_name": "脱敏现状调研确认记录",
                            "file_type": "MD",
                            "verification_status": "verified",
                        }
                    ],
                    "facts": [
                        {
                            "fact_id": fact_id,
                            "fact_key": fact_key,
                            "fact_category": fact_category,
                            "fact_content": fact_content,
                            "fact_status": "confirmed",
                            "materiality": "B",
                            "confirmation_required": False,
                            "evidence": [
                                {
                                    "source_id": source_id,
                                    "source_location": f"测试记录/{index}",
                                    "evidence_text": fact_content,
                                    "reliability_level": "A",
                                }
                            ],
                        }
                        for index, (
                            fact_id,
                            fact_key,
                            fact_category,
                            fact_content,
                        ) in enumerate(fact_specs, start=1)
                    ],
                },
            )

            plan_result = build_composition_plan(
                database, project_code, chapter_codes=["2.1.2"]
            )
            self.assertEqual(plan_result["ready_count"], 1)
            package_dir = root / "packages"
            export_packages(
                database,
                project_code,
                package_dir,
                chapter_codes=["2.1.2"],
            )
            package = json.loads(
                next(package_dir.glob("*.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(
                package["generation_contract"]["assembly_mode"],
                "current_state_evidence_synthesis",
            )

            result = build_initial_drafts(
                database,
                project_code,
                package_dir,
                root / "drafts",
                chapter_codes=["2.1.2"],
            )
            self.assertEqual(result["section_count"], 1)
            section = result["sections"][0]
            validation = validate_draft(
                database,
                project_code,
                "2.1.2",
                int(section["version_no"]),
                mode="working",
            )
            self.assertEqual(
                section["validation_status"],
                "passed",
                msg=json.dumps(validation, ensure_ascii=False, indent=2),
            )
            self.assertTrue(section["adopted"])
            draft = Path(section["draft"]).read_text(encoding="utf-8")
            self.assertIn("等级差异本身只用于确定核查范围", draft)
            self.assertIn("未参评表示尚无正式外部评级结果，不等于零级", draft)
            self.assertNotIn("已通过五级", draft)


if __name__ == "__main__":
    unittest.main()
