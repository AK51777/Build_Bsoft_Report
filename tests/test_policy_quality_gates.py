from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from validate_project_gates import policy_material_quality_checks  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from validate_full_report import PLACEHOLDER_PATTERN as FULL_REPORT_PLACEHOLDER_PATTERN  # noqa: E402
from validate_full_report import validate_report  # noqa: E402
from validate_section_draft import assess_content, policy_content_quality_issues  # noqa: E402
from build_evidence_bound_initial_drafts import policy_section  # noqa: E402
from build_policy_section_material import policy_material_signature  # noqa: E402


def material_fixture() -> dict:
    basis_counts = {
        "policy_basis": 16,
        "industry_standard": 20,
        "security_standard": 16,
        "investment_basis": 5,
    }
    basis_groups = {
        section: [{"policy_id": f"{section}-{index}"} for index in range(count)]
        for section, count in basis_counts.items()
    }
    background_counts = {"national": 8, "province": 2, "prefecture": 1}
    background_groups = {
        level: [{"policy_id": f"{level}-{index}"} for index in range(count)]
        for level, count in background_counts.items()
    }
    return {
        "policy_background_is_ordered_subsequence": True,
        "working_policy_background_is_ordered_subsequence": True,
        "policy_background_orphan_ids": [],
        "project_context": {"missing_required_context": []},
        "basis_groups": basis_groups,
        "policy_background_groups": background_groups,
        "delivery_eligible": True,
        "quality": {
            "formal_basis": {
                section: {
                    "count": count,
                    "minimum": count,
                    "gap": 0,
                    "overage": 0,
                }
                for section, count in basis_counts.items()
            },
            "formal_background": {
                level: {"count": count, "minimum": count, "gap": 0}
                for level, count in background_counts.items()
            },
            "delivery_blockers": [],
        },
    }


class PolicyQualityGateTests(unittest.TestCase):
    def test_policy_chapter_content_must_match_material_signature_and_items(self) -> None:
        material = material_fixture()
        material.update(
            {
                "project_name": "测试医院项目",
                "match_run_id": "RUN-1",
                "material_signature": "a" * 64,
                "basis_groups": {
                    "policy_basis": [
                        {
                            "policy_id": "POLICY-1",
                            "title": "已核验政策",
                            "document_no": "测试〔2026〕1号",
                            "clause_ids": ["CLAUSE-1"],
                        }
                    ],
                    "industry_standard": [
                        {
                            "policy_id": "STANDARD-1",
                            "title": "已核验行业标准",
                            "document_no": "WS/T 1—2026",
                            "clause_ids": ["CLAUSE-2"],
                        }
                    ],
                    "security_standard": [
                        {
                            "policy_id": "SECURITY-1",
                            "title": "已核验安全标准",
                            "document_no": "GB/T 1—2026",
                            "clause_ids": ["CLAUSE-3"],
                        }
                    ],
                    "investment_basis": [
                        {
                            "policy_id": "INVESTMENT-1",
                            "title": "已核验投资依据",
                            "document_no": "投测〔2026〕1号",
                            "clause_ids": ["CLAUSE-4"],
                        }
                    ],
                },
                "background_paragraphs": [],
            }
        )
        fake = (
            '<!-- policy-material chapter="1.2.1" match-run="RUN-OLD" signature="'
            + "b" * 64
            + '" -->\n'
            "| 1 | 《虚构政策》 | 假文号 | 正式采用 | "
            '<!-- policy-item id="FAKE" clauses="FAKE-CLAUSE" -->'
        )
        issues = policy_content_quality_issues(
            fake, "1.2.1", material, mode="delivery"
        )
        codes = {item["code"] for item in issues}
        self.assertIn("policy_material_signature_mismatch", codes)
        self.assertIn("policy_basis_items_mismatch", codes)
        self.assertIn("policy_basis_visible_row_count_mismatch", codes)

    def test_policy_background_paragraph_hash_and_extra_prose_are_blocking(self) -> None:
        from knowledge_db import sha256_text

        paragraph = "《已核验政策》提出与本项目一致的建设要求。"
        material = material_fixture()
        material.update(
            {
                "project_name": "测试医院项目",
                "match_run_id": "RUN-1",
                "material_signature": "a" * 64,
                "basis_groups": {},
                "background_paragraphs": [
                    {
                        "policy_id": "POLICY-1",
                        "title": "已核验政策",
                        "clause_ids": ["CLAUSE-1"],
                        "text": paragraph,
                        "text_hash": sha256_text(paragraph),
                    }
                ],
            }
        )
        content = (
            "测试医院项目政策背景按照国家、省或自治区、市或项目建设地区三个层级展开。"
            "本节政策顺序与前述政策类依据一致，且每段只改写已核验条款；仅有目录标题的文件不生成政策要求正文。\n\n"
            '<!-- policy-material chapter="2.1.1" match-run="RUN-1" signature="'
            + "a" * 64
            + '" -->\n\n#### 2.1.1.1 国家政策背景\n\n'
            '<!-- policy-background id="POLICY-1" clauses="CLAUSE-1" text-hash="'
            + sha256_text(paragraph)
            + '" -->\n'
            + paragraph
            + "\n\n虚构政策另行提出了没有条款支持的要求。"
        )
        issues = policy_content_quality_issues(
            content, "2.1.1", material, mode="delivery"
        )
        self.assertIn(
            "policy_background_unbound_prose", {item["code"] for item in issues}
        )

    def test_correct_basis_cannot_hide_extra_reference_row_or_policy_prose(self) -> None:
        material = material_fixture()
        material.update(
            {
                "project_name": "测试医院项目",
                "match_run_id": "RUN-1",
                "material_signature": "a" * 64,
                "basis_groups": {
                    "policy_basis": [
                        {
                            "policy_id": "POLICY-1",
                            "title": "已核验政策",
                            "document_no": "测试〔2026〕1号",
                            "clause_ids": ["CLAUSE-1"],
                        }
                    ],
                    "industry_standard": [
                        {
                            "policy_id": "STANDARD-1",
                            "title": "已核验行业标准",
                            "document_no": "WS/T 1—2026",
                            "clause_ids": ["CLAUSE-2"],
                        }
                    ],
                    "security_standard": [
                        {
                            "policy_id": "SECURITY-1",
                            "title": "已核验安全标准",
                            "document_no": "GB/T 1—2026",
                            "clause_ids": ["CLAUSE-3"],
                        }
                    ],
                    "investment_basis": [
                        {
                            "policy_id": "INVESTMENT-1",
                            "title": "已核验投资依据",
                            "document_no": "投测〔2026〕1号",
                            "clause_ids": ["CLAUSE-4"],
                        }
                    ],
                },
                "working_basis_groups": {},
            }
        )
        content = "\n".join(
            policy_section(
                {"official_name": "测试医院项目"},
                {"chapter_code": "1.2.1"},
                material,
                ["编制依据表"],
            )
        )
        clean_codes = {
            item["code"]
            for item in policy_content_quality_issues(
                content,
                "1.2.1",
                material,
                mode="delivery",
                check_quality_gates=False,
            )
        }
        self.assertNotIn("policy_basis_unbound_content", clean_codes)

        attacked = (
            content
            + "\n| 99 | 《虚构政策》 | 假文号 | 参考采用 |"
            + "\n《虚构政策》明确提出了无条款支持的强制要求。"
        )
        attacked_codes = {
            item["code"]
            for item in policy_content_quality_issues(
                attacked,
                "1.2.1",
                material,
                mode="delivery",
                check_quality_gates=False,
            )
        }
        self.assertIn("policy_basis_visible_row_count_mismatch", attacked_codes)
        self.assertIn("policy_basis_unbound_content", attacked_codes)

    def test_policy_background_rejects_extra_semantic_heading(self) -> None:
        from knowledge_db import sha256_text

        paragraph = "《已核验政策》提出与本项目一致的建设要求。"
        material = material_fixture()
        material.update(
            {
                "project_name": "测试医院项目",
                "match_run_id": "RUN-1",
                "material_signature": "a" * 64,
                "background_paragraphs": [
                    {
                        "policy_id": "POLICY-1",
                        "title": "已核验政策",
                        "clause_ids": ["CLAUSE-1"],
                        "text": paragraph,
                        "text_hash": sha256_text(paragraph),
                    }
                ],
            }
        )
        content = (
            "测试医院项目政策背景按照国家、省或自治区、市或项目建设地区三个层级展开。"
            "本节政策顺序与前述政策类依据一致，且每段只改写已核验条款；仅有目录标题的文件不生成政策要求正文。\n\n"
            '<!-- policy-material chapter="2.1.1" match-run="RUN-1" signature="'
            + "a" * 64
            + '" -->\n\n#### 2.1.1.1 国家政策背景\n\n'
            '<!-- policy-background id="POLICY-1" clauses="CLAUSE-1" text-hash="'
            + sha256_text(paragraph)
            + '" -->\n'
            + paragraph
            + "\n\n#### 2.1.1.2 省/自治区政策背景"
            + "\n\n#### 2.1.1.3 市/项目建设地区政策背景"
            + "\n\n#### 《虚构政策》强制要求项目立即达标"
        )
        codes = {
            item["code"]
            for item in policy_content_quality_issues(
                content,
                "2.1.1",
                material,
                mode="delivery",
                check_quality_gates=False,
            )
        }
        self.assertIn("policy_background_heading_mismatch", codes)

    def test_passes_only_when_hard_minimums_and_background_chain_hold(self) -> None:
        checks = {item["gate_code"]: item for item in policy_material_quality_checks(material_fixture())}
        self.assertEqual(checks["GATE-BASIS-QUANTITY"]["result"], "pass")
        self.assertEqual(checks["GATE-POLICY-BACKGROUND-CHAIN"]["result"], "pass")
        self.assertEqual(checks["GATE-POLICY-MATERIAL-ELIGIBILITY"]["result"], "pass")

    def test_basis_shortfall_is_blocking_and_not_hidden_by_total_count(self) -> None:
        material = material_fixture()
        material["quality"]["formal_basis"]["security_standard"].update(
            {"count": 15, "gap": 1}
        )
        material["basis_groups"]["security_standard"].pop()
        checks = {item["gate_code"]: item for item in policy_material_quality_checks(material)}
        self.assertEqual(checks["GATE-BASIS-QUANTITY"]["result"], "fail")
        self.assertEqual(checks["GATE-BASIS-QUANTITY"]["blocking_count"], 1)

    def test_background_order_or_context_gap_is_blocking(self) -> None:
        material = material_fixture()
        material["working_policy_background_is_ordered_subsequence"] = False
        material["project_context"]["missing_required_context"] = ["jurisdiction.prefecture"]
        checks = {item["gate_code"]: item for item in policy_material_quality_checks(material)}
        self.assertEqual(checks["GATE-POLICY-BACKGROUND-CHAIN"]["result"], "fail")
        self.assertEqual(checks["GATE-POLICY-BACKGROUND-CHAIN"]["blocking_count"], 2)

    def test_missing_material_fails_both_policy_quality_gates(self) -> None:
        checks = policy_material_quality_checks(None, material_error="completed run missing")
        self.assertEqual([item["result"] for item in checks], ["fail", "fail", "fail"])

    def test_material_blockers_cannot_be_hidden_by_satisfied_counts(self) -> None:
        for blocker in (
            "duplicate_formal_basis_documents:1",
            "policy_clause_section_permission_violations:1",
            "policy_background_summary_missing:1",
            "policy_forbidden_claim_violations:1",
            "stale_policy_match_violations:1",
        ):
            with self.subTest(blocker=blocker):
                material = material_fixture()
                material["delivery_eligible"] = False
                material["quality"]["delivery_blockers"] = [blocker]
                checks = {
                    item["gate_code"]: item
                    for item in policy_material_quality_checks(material)
                }
                self.assertEqual(
                    checks["GATE-POLICY-MATERIAL-ELIGIBILITY"]["result"], "fail"
                )
                self.assertGreater(
                    checks["GATE-POLICY-MATERIAL-ELIGIBILITY"]["blocking_count"], 0
                )

    def test_material_signature_changes_when_delivery_diagnostics_change(self) -> None:
        clean = material_fixture()
        clean.update(
            {
                "project_code": "TEST",
                "basis_profile_version": "PROFILE-1",
                "match_run_id": "RUN-1",
                "background_paragraphs": [],
            }
        )
        blocked = material_fixture()
        blocked.update(
            {
                "project_code": "TEST",
                "basis_profile_version": "PROFILE-1",
                "match_run_id": "RUN-1",
                "background_paragraphs": [],
                "delivery_eligible": False,
                "section_permission_violations": [
                    {"policy_id": "P1", "clause_id": "C1", "section": "basis"}
                ],
            }
        )
        blocked["quality"]["delivery_blockers"] = [
            "policy_clause_section_permission_violations:1"
        ]
        self.assertNotEqual(
            policy_material_signature(clean), policy_material_signature(blocked)
        )

    def test_reported_quantity_cannot_override_actual_basis_rows(self) -> None:
        material = material_fixture()
        material["basis_groups"]["security_standard"].pop()
        material["quality"]["formal_basis"]["security_standard"].update(
            {"count": 16, "gap": 0}
        )
        checks = {
            item["gate_code"]: item for item in policy_material_quality_checks(material)
        }
        self.assertEqual(checks["GATE-BASIS-QUANTITY"]["result"], "fail")

    def test_delivery_validation_cannot_bypass_policy_quality_gates(self) -> None:
        blueprint = {
            "document_type": "feasibility_study",
            "blueprints": [
                {
                    "section_role": role,
                    "purpose": "测试政策证据链。",
                    "required_questions": [],
                    "required_fact_categories": [],
                    "required_scope_types": [],
                    "required_policy_topics": [],
                    "required_tables": [],
                    "forbidden_content": [],
                    "completion_rules": [],
                }
                for role in ("basis", "policy_background")
            ],
            "outline": [
                {
                    "chapter_code": "1.2.1",
                    "section_title": "可行性研究报告编制依据",
                    "section_role": "basis",
                },
                {
                    "chapter_code": "2.1.1",
                    "section_title": "政策背景",
                    "section_role": "policy_background",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-DELIVERY-GATE",
                official_name="政策交付门禁测试项目",
            )
            database = Path(initialized["database"])
            build_composition_plan(
                database,
                "POLICY-DELIVERY-GATE",
                blueprint_payload=blueprint,
            )
            result = validate_report(
                database,
                "POLICY-DELIVERY-GATE",
                mode="delivery",
            )

        locations = {item["location"] for item in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("GATE-BASIS-QUANTITY", locations)
        self.assertIn("GATE-POLICY-BACKGROUND-CHAIN", locations)

    def test_unverified_candidate_is_working_only_and_blocks_delivery(self) -> None:
        plan = {
            "chapter_code": "1.2.1",
            "section_role": "basis",
            "length_min": 0,
            "required_tables_json": "[]",
            "outline_nodes": [],
        }
        content = "#### 1.2.1.1 政策类依据\n\n1. 《候选政策》【待核验】；"

        working = assess_content(content, plan, [], mode="working")
        delivery = assess_content(content, plan, [], mode="delivery")

        self.assertEqual(working["status"], "passed")
        self.assertGreaterEqual(working["warning_count"], 1)
        self.assertEqual(delivery["status"], "failed")
        self.assertIn(
            "unresolved_placeholder",
            {item["code"] for item in delivery["issues"]},
        )
        self.assertIsNotNone(FULL_REPORT_PLACEHOLDER_PATTERN.search(content))


if __name__ == "__main__":
    unittest.main()
