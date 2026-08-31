from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_policy_section_material import (  # noqa: E402
    bounded_background_candidate_groups,
    build_material,
    deduplicate_formal_basis,
)
from ingest_policies import ingest  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from knowledge_db import connect  # noqa: E402
from match_project_policies import match, policy_match_input_signature  # noqa: E402
from match_project_policies import permits_section  # noqa: E402
from run_project_pipeline import reusable_policy_run  # noqa: E402


class PolicySectionMaterialTests(unittest.TestCase):
    def test_background_candidates_are_capped_per_jurisdiction_level(self) -> None:
        items = [
            {
                "basis_section": "policy_basis",
                "suggested_use": "background",
                "counts_toward_minimum": True,
                "jurisdiction_level": "national",
                "title": f"国家政策{index}",
            }
            for index in range(28)
        ]
        groups = bounded_background_candidate_groups(
            items,
            {"jurisdiction": {}},
            {
                "national": {"maximum": 16},
                "province": {"maximum": 5},
                "prefecture": {"maximum": 4},
            },
        )
        self.assertEqual(len(groups["national"]), 16)
        self.assertEqual(groups["province"], [])
        self.assertEqual(groups["prefecture"], [])

    def test_clause_section_permission_is_explicit_and_alias_aware(self) -> None:
        self.assertTrue(permits_section('["basis"]', "basis", "1.2.1"))
        self.assertTrue(permits_section(["2.1.1"], "policy_background", "2.1.1"))
        self.assertFalse(permits_section('["security_design"]', "basis", "1.2.1"))
        self.assertFalse(permits_section("[]", "policy_background", "2.1.1"))

    def test_formal_basis_cannot_be_padded_by_duplicate_source_documents(self) -> None:
        entries = [
            {
                "policy_id": "POLICY-1",
                "title": "政策甲",
                "document_no": "国办发〔2026〕1号",
                "official_url": "https://example.gov.cn/a",
            },
            {
                "policy_id": "POLICY-2",
                "title": "政策甲复制记录",
                "document_no": "国办发 [2026] 1号",
                "official_url": "https://example.gov.cn/a?copy=1",
            },
            {
                "policy_id": "POLICY-3",
                "title": "政策乙",
                "document_no": "",
                "official_url": "https://example.gov.cn/b?source=portal",
            },
            {
                "policy_id": "POLICY-4",
                "title": "政策乙镜像",
                "document_no": "",
                "official_url": "https://example.gov.cn/b#content",
            },
        ]
        unique, duplicates = deduplicate_formal_basis(entries)
        self.assertEqual([item["policy_id"] for item in unique], ["POLICY-1", "POLICY-3"])
        self.assertEqual(len(duplicates), 2)
        self.assertEqual(
            {item["duplicate_policy_id"] for item in duplicates},
            {"POLICY-2", "POLICY-4"},
        )

    def test_empty_policy_material_is_not_delivery_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-MATERIAL-EMPTY",
                official_name="空政策材料测试项目",
            )
            database = Path(initialized["database"])
            seed = json.loads(
                (
                    SKILL_ROOT
                    / "assets"
                    / "knowledge-base"
                    / "seeds"
                    / "core_policy_seed_20260804.json"
                ).read_text(encoding="utf-8")
            )
            ingest(database, seed)
            match(database, "POLICY-MATERIAL-EMPTY", {"不存在的主题"}, None, None)

            material = build_material(database, "POLICY-MATERIAL-EMPTY", mode="working")

            self.assertFalse(material["basis_entries"])
            self.assertFalse(material["background_paragraphs"])
            self.assertFalse(material["delivery_eligible"])
            fixed_ids = {
                item["basis_id"]
                for items in material["fixed_basis_groups"].values()
                for item in items
            }
            self.assertTrue(
                {f"IND-{index:03d}" for index in range(21, 28)}.isdisjoint(fixed_ids)
            )

    def test_old_province_match_is_excluded_after_project_jurisdiction_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-MATERIAL-JURISDICTION",
                official_name="政策属地变更测试项目",
                jurisdiction_code="540400",
                jurisdiction_name="林芝市",
            )
            database = Path(initialized["database"])
            seed = json.loads(
                (
                    SKILL_ROOT
                    / "assets"
                    / "knowledge-base"
                    / "seeds"
                    / "core_policy_seed_20260804.json"
                ).read_text(encoding="utf-8")
            )
            policy = json.loads(json.dumps(seed["policies"][0], ensure_ascii=False))
            policy.update(
                {
                    "policy_id": "POLICY-TIBET-TEST",
                    "title": "西藏自治区智慧医院建设测试政策",
                    "document_no": "藏测〔2026〕1号",
                    "issuer": "西藏自治区测试部门",
                    "jurisdiction_level": "province",
                    "jurisdiction_code": "540000",
                    "jurisdiction_name": "西藏自治区",
                    "official_url": "https://example.xizang.gov.cn/policy-test",
                }
            )
            policy["clauses"][0]["clause_id"] = "CLAUSE-TIBET-TEST"
            ingest(database, {"policies": [policy]})
            match_result = match(
                database,
                "POLICY-MATERIAL-JURISDICTION",
                {"smart_hospital"},
                None,
                None,
            )

            with connect(database) as connection:
                project_row = connection.execute(
                    "SELECT * FROM project WHERE project_code='POLICY-MATERIAL-JURISDICTION'"
                ).fetchone()
                initial_signature = policy_match_input_signature(
                    connection, project_row, {"smart_hospital"}
                )
                reusable = reusable_policy_run(
                    connection,
                    project_row["project_id"],
                    '["smart_hospital"]',
                    initial_signature,
                )
            self.assertEqual(reusable["match_run_id"], match_result["match_run_id"])

            before = build_material(database, "POLICY-MATERIAL-JURISDICTION", mode="working")
            self.assertIn(
                "POLICY-TIBET-TEST",
                {item["policy_id"] for item in before["basis_entries"]},
            )

            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE project SET jurisdiction_code='510100',jurisdiction_name='成都市'"
                )
                connection.commit()
            finally:
                connection.close()

            after = build_material(database, "POLICY-MATERIAL-JURISDICTION", mode="working")
            self.assertNotIn(
                "POLICY-TIBET-TEST",
                {item["policy_id"] for item in after["basis_entries"]},
            )
            self.assertEqual(
                after["stale_policy_match_violations"][0]["reasons"],
                ["jurisdiction_no_longer_applies"],
            )
            self.assertIn(
                "stale_policy_match_violations:1",
                after["quality"]["delivery_blockers"],
            )
            with connect(database) as connection:
                changed_project = connection.execute(
                    "SELECT * FROM project WHERE project_code='POLICY-MATERIAL-JURISDICTION'"
                ).fetchone()
                changed_signature = policy_match_input_signature(
                    connection, changed_project, {"smart_hospital"}
                )
                self.assertNotEqual(initial_signature, changed_signature)
                self.assertIsNone(
                    reusable_policy_run(
                        connection,
                        changed_project["project_id"],
                        '["smart_hospital"]',
                        changed_signature,
                    )
                )

    def test_working_material_is_traceable_and_delivery_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project",
                project_code="POLICY-MATERIAL-001",
                official_name="政策材料测试项目",
                jurisdiction_code="100000",
                jurisdiction_name="全国",
            )
            database = Path(initialized["database"])
            seed = json.loads(
                (
                    SKILL_ROOT
                    / "assets"
                    / "knowledge-base"
                    / "seeds"
                    / "core_policy_seed_20260804.json"
                ).read_text(encoding="utf-8")
            )
            ingest(database, seed)
            topics = {
                topic
                for policy in seed["policies"]
                for clause in policy["clauses"]
                for topic in clause["topic_tags"]
            }
            match(database, "POLICY-MATERIAL-001", topics, None, None)

            working = build_material(database, "POLICY-MATERIAL-001", mode="working")
            self.assertTrue(working["basis_entries"])
            self.assertTrue(working["background_paragraphs"])
            self.assertFalse(working["delivery_eligible"])
            self.assertEqual(
                working["policy_background_order"],
                [
                    policy_id
                    for policy_id in working["policy_basis_order"]
                    if policy_id in set(working["policy_background_order"])
                ],
            )
            self.assertTrue(working["policy_background_is_ordered_subsequence"])
            self.assertTrue(working["working_policy_background_is_ordered_subsequence"])
            first = working["background_paragraphs"][0]
            self.assertTrue(first["clause_ids"])
            self.assertIn("文件中与本项目相关的要求包括", first["text"])
            self.assertNotIn("经核验的相关条款主要包括", first["text"])
            self.assertTrue(first["text_hash"])
            with self.assertRaisesRegex(ValueError, "unconfirmed"):
                build_material(database, "POLICY-MATERIAL-001", mode="delivery")

            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    UPDATE project_policy_match
                    SET decision_status='user_confirmed',decision_reason='测试确认'
                    WHERE basis_use=1
                    """
                )
                connection.commit()
            finally:
                connection.close()
            delivery = build_material(database, "POLICY-MATERIAL-001", mode="delivery")
            self.assertFalse(delivery["delivery_eligible"])
            self.assertFalse(delivery["unconfirmed_match_ids"])
            self.assertTrue(
                all(item["delivery_eligible"] for item in delivery["background_paragraphs"])
            )
            self.assertTrue(
                any(
                    blocker.startswith("formal_policy_basis_gap:")
                    for blocker in delivery["quality"]["delivery_blockers"]
                )
            )
            self.assertTrue(delivery["policy_background_is_ordered_subsequence"])


if __name__ == "__main__":
    unittest.main()
