from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from import_policy_catalog_sqlite import import_catalog  # noqa: E402
from ingest_project_facts import ingest as ingest_facts  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from knowledge_db import connect, now_iso  # noqa: E402
from match_policy_catalog_candidates import (  # noqa: E402
    candidate_jurisdiction_applies,
    load_basis_profile,
    load_project_policy_context,
    match_candidates,
    normalize_fixed_groups,
    score_entry,
    select_bounded_group_candidates,
)
from run_project_pipeline import project_policy_topics  # noqa: E402


POLICY_TITLES = [
    "《中华人民共和国国民经济和社会发展第十五个五年规划纲要》",
    "《数字中国建设整体布局规划》",
    "《“健康中国2030”规划纲要》",
    "《关于进一步完善医疗卫生服务体系的意见》",
    "《国务院办公厅关于推动公立医院高质量发展的意见》",
    "《关于抓好推动公立医院高质量发展意见落实的通知》",
    "《关于印发公立医院高质量发展评价指标（试行）的通知》",
    "《国务院办公厅关于促进“互联网+医疗健康”发展的意见》",
    "《关于印发互联网诊疗监管细则（试行）的通知》",
    "《关于深入推进“互联网+医疗健康”“五个一”服务行动的通知》",
    "《关于印发全民健康信息平台体系建设应用指南的通知》",
    "《关于加强全民健康信息标准化体系建设的意见》",
    "《关于进一步推进以电子病历为核心的医疗机构信息化建设工作的通知》",
    "《关于进一步完善预约诊疗制度 加强智慧医院建设的通知》",
    "《关于进一步加强医疗机构电子病历信息使用管理的通知》",
    "《关于印发医疗机构检查检验结果互认管理办法的通知》",
    "《关于进一步推进医疗机构检查检验结果互认的指导意见》",
    "《卫生健康统计工作管理办法》",
    "《关于印发全国医院上报数据统计分析指标集（试行）的通知》",
    "《关于印发<国家数据标准体系建设指南>的通知》",
]


def catalog_payload() -> dict:
    titles = POLICY_TITLES + [
        "《关于印发全国医院信息化建设标准与规范（试行）的通知》",
        "《关于印发医疗卫生机构网络安全管理办法的通知》",
        "《网络数据安全管理条例》",
        "《关于修订印发社会领域中央预算内投资相关专项管理办法的通知》",
        "《关于<个人信息保护合规审计管理办法（征求意见稿）>公开征求意见的通知》",
    ]
    records = []
    for index, title in enumerate(titles, 1):
        records.append(
            {
                "catalog_entry_id": f"POLICYCATENTRY-PROFILE-{index:03d}",
                "source_row": index + 3,
                "source_index_no": f"TEST.{index:03d}",
                "identity_key": f"POLICYIDENTITY-PROFILE-{index:03d}",
                "catalog_group_code": "TEST",
                "catalog_group_name": "医院信息化政策目录",
                "authority_level_label": "国家" if index <= 5 else "部委",
                "category_name": "信息化与统计上报",
                "keyword_text": "医院 信息化 电子病历 互联互通 网络安全",
                "keyword_tags": [],
                "document_no": f"测试文号〔2026〕{index}号",
                "title": title,
                "publish_date": f"2026-01-{(index - 1) % 28 + 1:02d}",
                "publish_date_raw": "2026",
                "issuer": "测试发布机关",
                "file_count": 1,
                "notes": "",
                "external_url": f"https://example.gov.cn/policy/{index}",
                "verification_status": "unverified",
                "entry_status": "active",
                "row_hash": f"row-hash-{index}",
            }
        )
    duplicate = dict(records[4])
    duplicate.update(
        {
            "catalog_entry_id": "POLICYCATENTRY-PROFILE-DUP",
            "source_row": 999,
            "source_index_no": "TEST.DUP",
            "identity_key": "POLICYIDENTITY-PROFILE-DUP",
            "row_hash": "row-hash-dup",
        }
    )
    records.append(duplicate)
    return {
        "schema_version": "1.0",
        "catalog_id": "POLICYCATALOG-PROFILE-TEST",
        "catalog_scope": "medical_health_national",
        "title": "智慧医院政策目录测试",
        "permission_scope": "internal_company_reference",
        "source_file": {"file_name": "policy.xlsx", "sha256": "a" * 64},
        "worksheet_name": "政策",
        "records": records,
        "built_at": "2026-08-28T00:00:00+08:00",
        "content_hash": "c" * 64,
    }


class PolicyBasisProfileTests(unittest.TestCase):
    def test_policy_limit_reserves_existing_province_and_prefecture_background(self) -> None:
        rows = []
        for level, count in (("national", 35), ("province", 3), ("prefecture", 2)):
            for index in range(count):
                rows.append(
                    {
                        "title": f"{level}-{index}",
                        "document_no": f"{level}-{index}",
                        "suggested_use": "background",
                        "jurisdiction_level": level,
                    }
                )
        rules = load_basis_profile()["policy_background_quantity_rules"]
        selected = select_bounded_group_candidates(rows, "policy_basis", 28, rules)
        self.assertEqual(len(selected), 28)
        self.assertEqual(
            sum(item["jurisdiction_level"] == "province" for item in selected), 3
        )
        self.assertEqual(
            sum(item["jurisdiction_level"] == "prefecture" for item in selected), 2
        )
        self.assertGreaterEqual(
            sum(item["jurisdiction_level"] == "national" for item in selected), 16
        )

    def test_catalog_jurisdiction_is_hard_filtered_by_project_code_or_name(self) -> None:
        context = {
            "jurisdiction": {
                "code": "540400",
                "name": "林芝市",
                "province": "西藏自治区",
                "province_code": "540000",
                "prefecture": "林芝市",
                "prefecture_code": "540400",
            }
        }
        self.assertTrue(
            candidate_jurisdiction_applies({"jurisdiction_level": "national"}, context)
        )
        self.assertTrue(
            candidate_jurisdiction_applies(
                {"jurisdiction_level": "province", "jurisdiction_code": "540000"},
                context,
            )
        )
        self.assertFalse(
            candidate_jurisdiction_applies(
                {"jurisdiction_level": "province", "jurisdiction_code": "510000"},
                context,
            )
        )
        self.assertTrue(
            candidate_jurisdiction_applies(
                {"jurisdiction_level": "prefecture", "jurisdiction_name": "林芝市"},
                context,
            )
        )
        self.assertFalse(
            candidate_jurisdiction_applies(
                {"jurisdiction_level": "unclassified"}, context
            )
        )

    def test_pipeline_default_topics_are_controlled_by_project_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-PROFILE-CONTROLLED-TOPICS",
                project_type="hospital_informationization",
            )
            topics = project_policy_topics(
                Path(initialized["database"]),
                "POLICY-PROFILE-CONTROLLED-TOPICS",
                set(),
            )
        self.assertIn("hospital_informationization", topics)
        self.assertIn("cybersecurity", topics)
        self.assertNotIn("internet_health", topics)
        self.assertNotIn("internet_hospital", topics)
        self.assertNotIn("telemedicine", topics)
        self.assertNotIn("medical_ai", topics)

    def test_pending_scope_cannot_trigger_formal_specialty_topics_or_fixed_standards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-PROFILE-PENDING-SCOPE",
                project_type="hospital_informationization",
            )
            database = Path(initialized["database"])
            with connect(database) as connection:
                project = connection.execute(
                    "SELECT * FROM project WHERE project_code=?",
                    ("POLICY-PROFILE-PENDING-SCOPE",),
                ).fetchone()
                timestamp = now_iso()
                connection.execute(
                    """
                    INSERT INTO project_scope_item (
                      scope_id,project_id,original_name,standard_name,domain,item_type,
                      construction_mode,customer_scope,status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "SCOPE-PENDING-TELEMEDICINE",
                        project["project_id"],
                        "远程医疗平台",
                        "远程医疗平台",
                        "临床服务",
                        "application",
                        "pending_confirmation",
                        1,
                        "pending_confirmation",
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
                context = load_project_policy_context(connection, project)

            topics = project_policy_topics(
                database,
                "POLICY-PROFILE-PENDING-SCOPE",
                set(),
            )
            fixed_ids = {
                item["basis_id"]
                for rows in normalize_fixed_groups(load_basis_profile(), topics).values()
                for item in rows
            }

        self.assertEqual(context["scope_topics"], [])
        self.assertEqual(context["scope_bindings"], [])
        self.assertNotIn("telemedicine", topics)
        self.assertNotIn("internet_health", topics)
        self.assertTrue(
            {f"IND-{index:03d}" for index in range(24, 28)}.isdisjoint(fixed_ids)
        )

    def test_profile_has_confirmed_hard_minimums_and_clean_fixed_groups(self) -> None:
        profile = load_basis_profile()
        self.assertEqual(profile["quantity_rules"]["policy_basis"]["minimum"], 16)
        self.assertEqual(profile["quantity_rules"]["industry_standard"]["minimum"], 20)
        self.assertEqual(profile["quantity_rules"]["security_standard"]["minimum"], 16)
        groups = normalize_fixed_groups(profile)
        self.assertGreaterEqual(len(groups["industry_standard"]), 20)
        self.assertGreaterEqual(len(groups["security_standard"]), 16)
        self.assertGreaterEqual(len(groups["investment_basis"]), 5)
        for section, rows in groups.items():
            self.assertEqual(len({row["basis_id"] for row in rows}), len(rows), section)
            self.assertEqual(
                len({(row["title"], row["document_no"]) for row in rows}), len(rows), section
            )
            self.assertTrue(all("征求意见" not in row["title"] for row in rows))
            self.assertTrue(all(row["candidate_only"] for row in rows))
            self.assertTrue(all(not row["delivery_eligible"] for row in rows))

    def test_specialty_candidates_require_scope_triggered_topics(self) -> None:
        profile = load_basis_profile()
        default_topics = set(profile["default_topics"])
        entry = next(
            row
            for row in catalog_payload()["records"]
            if "促进“互联网+医疗健康”" in row["title"]
        )
        context = {
            "institution_type": "综合医院",
            "hospital_grade": "",
            "ownership": "公立医院",
            "report_type": "government_investment_feasibility_study",
            "investment_regime": "government_investment",
        }
        self.assertIsNone(score_entry(entry, default_topics, profile, context))
        self.assertIsNotNone(
            score_entry(entry, default_topics | {"internet_health"}, profile, context)
        )

        default_fixed_ids = {
            row["basis_id"]
            for rows in normalize_fixed_groups(profile, default_topics).values()
            for row in rows
        }
        self.assertTrue(
            {f"IND-{index:03d}" for index in range(21, 28)}.isdisjoint(default_fixed_ids)
        )
        scoped_fixed_ids = {
            row["basis_id"]
            for rows in normalize_fixed_groups(
                profile, default_topics | {"medical_imaging", "telemedicine"}
            ).values()
            for row in rows
        }
        self.assertTrue(
            {f"IND-{index:03d}" for index in range(21, 28)}.issubset(scoped_fixed_ids)
        )

    def test_matcher_builds_four_sections_without_counting_duplicates_or_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-PROFILE-001",
                official_name="测试市人民医院智慧医院项目",
                jurisdiction_code="540400",
                jurisdiction_name="林芝市",
            )
            database = Path(initialized["database"])
            ingest_facts(
                database,
                {
                    "project": {
                        "project_code": "POLICY-PROFILE-001",
                        "official_name": "测试市人民医院智慧医院项目",
                        "jurisdiction_code": "540400",
                        "jurisdiction_name": "林芝市",
                        "project_type": "hospital_informationization",
                    },
                    "facts": [
                        {
                            "fact_id": "FACT-PROVINCE",
                            "fact_key": "jurisdiction.province",
                            "fact_category": "project",
                            "fact_content": "西藏自治区",
                            "normalized_value": "西藏自治区",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                        {
                            "fact_id": "FACT-INSTITUTION-TYPE",
                            "fact_key": "organization.institution_type",
                            "fact_category": "organization",
                            "fact_content": "综合医院",
                            "normalized_value": "综合医院",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                        {
                            "fact_id": "FACT-OWNERSHIP",
                            "fact_key": "organization.ownership",
                            "fact_category": "organization",
                            "fact_content": "公立医院",
                            "normalized_value": "公立医院",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                        {
                            "fact_id": "FACT-REPORT-TYPE",
                            "fact_key": "project.report_type",
                            "fact_category": "project",
                            "fact_content": "政府投资项目可行性研究报告",
                            "normalized_value": "government_investment_feasibility_study",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                        {
                            "fact_id": "FACT-INVESTMENT-REGIME",
                            "fact_key": "investment.regime",
                            "fact_category": "investment",
                            "fact_content": "政府投资",
                            "normalized_value": "government_investment",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                        {
                            "fact_id": "FACT-PREFECTURE",
                            "fact_key": "jurisdiction.prefecture",
                            "fact_category": "project",
                            "fact_content": "林芝市",
                            "normalized_value": "林芝市",
                            "fact_status": "confirmed",
                            "confirmation_required": False,
                        },
                    ],
                },
            )
            timestamp = now_iso()
            with connect(database) as connection:
                project_id = connection.execute(
                    "SELECT project_id FROM project WHERE project_code=?",
                    ("POLICY-PROFILE-001",),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO project_scope_item (
                      scope_id,project_id,original_name,standard_name,domain,item_type,
                      construction_mode,customer_scope,status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "SCOPE-INTERNET-HOSPITAL",
                        project_id,
                        "互联网医院平台",
                        "互联网医院平台",
                        "患者服务",
                        "application",
                        "new_build",
                        1,
                        "confirmed",
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
            import_catalog(database, catalog_payload())
            result = match_candidates(database, "POLICY-PROFILE-001")
            pipeline_topics = project_policy_topics(
                database,
                "POLICY-PROFILE-001",
                {"hospital_informationization"},
            )

        policy_rows = [
            row for row in result["candidates"] if row["basis_section"] == "policy_basis"
        ]
        self.assertGreaterEqual(len(policy_rows), 16)
        self.assertEqual(
            len([row for row in policy_rows if row["title"] == POLICY_TITLES[4]]),
            1,
        )
        self.assertTrue(all("征求意见" not in row["title"] for row in result["candidates"]))
        self.assertEqual(result["project_context"]["jurisdiction"]["province"], "西藏自治区")
        self.assertEqual(result["project_context"]["jurisdiction"]["prefecture"], "林芝市")
        self.assertEqual(result["project_context"]["missing_required_context"], [])
        self.assertIn("internet_health", result["project_context"]["scope_topics"])
        self.assertIn("internet_health", pipeline_topics)
        self.assertEqual(
            [item["scope_id"] for item in result["project_context"]["scope_bindings"]],
            ["SCOPE-INTERNET-HOSPITAL"],
        )
        internet_policy = next(
            row for row in policy_rows if "互联网诊疗监管细则" in row["title"]
        )
        self.assertTrue(internet_policy["counts_toward_minimum"])
        quality = result["quality"]
        self.assertEqual(quality["candidate_gate"], "passed")
        self.assertEqual(quality["delivery_gate"], "blocked")
        self.assertEqual(quality["groups"]["policy_basis"]["status"], "candidate_ready")
        self.assertEqual(quality["groups"]["industry_standard"]["status"], "candidate_ready")
        self.assertEqual(quality["groups"]["security_standard"]["status"], "candidate_ready")
        self.assertIn(
            "catalog_candidates_are_unverified_and_cannot_support_delivery",
            quality["delivery_blockers"],
        )

    def test_missing_province_is_exposed_instead_of_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-PROFILE-MISSING-JURISDICTION",
                jurisdiction_name="林芝市",
            )
            database = Path(initialized["database"])
            import_catalog(database, catalog_payload())
            result = match_candidates(database, "POLICY-PROFILE-MISSING-JURISDICTION")
        self.assertEqual(
            result["project_context"]["missing_required_context"],
            [
                "jurisdiction.province",
                "organization.institution_type",
                "organization.ownership",
                "project.report_type",
                "investment.regime",
            ],
        )


if __name__ == "__main__":
    unittest.main()
