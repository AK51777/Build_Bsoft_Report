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

from build_policy_section_material import build_material  # noqa: E402
from ingest_policies import ingest  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from match_project_policies import match  # noqa: E402


class PolicySectionMaterialTests(unittest.TestCase):
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
            self.assertTrue(delivery["delivery_eligible"])
            self.assertFalse(delivery["unconfirmed_match_ids"])
            self.assertTrue(
                all(item["delivery_eligible"] for item in delivery["background_paragraphs"])
            )


if __name__ == "__main__":
    unittest.main()
