from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_policy_section_material import build_material, to_markdown as material_markdown  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from import_policy_catalog_sqlite import import_catalog  # noqa: E402
from ingest_policies import ingest as ingest_policies  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402
from knowledge_db import connect  # noqa: E402
from match_policy_catalog_candidates import match_candidates, select_project_catalog  # noqa: E402
from match_project_policies import match as match_project_policies  # noqa: E402
from sync_postgres_knowledge_snapshot import record_snapshot  # noqa: E402


def catalog_payload() -> dict:
    titles = [
        ("CN1.001", "《关于推动公立医院高质量发展的意见》", "国办发〔2021〕18号"),
        ("CN2.001", "《关于印发全国医院信息化建设标准与规范（试行）的通知》", "国卫办规划发〔2018〕4号"),
        ("CN3.001", "《关于印发电子病历系统应用水平分级评价管理办法（试行）及评价标准（试行）的通知》", "国卫办医函〔2018〕1079号"),
        ("CN4.001", "《关于印发“十四五”全民健康信息化规划的通知》", "国卫规划发〔2022〕30号"),
    ]
    records = []
    for index, (source_index_no, title, document_no) in enumerate(titles, start=4):
        records.append(
            {
                "catalog_entry_id": f"POLICYCATENTRY-{index}",
                "source_row": index,
                "source_index_no": source_index_no,
                "identity_key": f"POLICYIDENTITY-{index}",
                "catalog_group_code": source_index_no.split(".")[0],
                "catalog_group_name": "测试目录",
                "authority_level_label": "国家",
                "category_name": "信息化",
                "keyword_text": "",
                "keyword_tags": [],
                "document_no": document_no,
                "title": title,
                "publish_date": "2022-01-01",
                "publish_date_raw": "2022-01-01",
                "issuer": "国家卫生健康委",
                "file_count": 1,
                "notes": "",
                "external_url": f"https://example.gov.cn/{index}",
                "verification_status": "unverified",
                "entry_status": "active",
                "row_hash": f"row-hash-{index}",
            }
        )
    return {
        "schema_version": "1.0",
        "catalog_id": "POLICYCATALOG-LOCAL-TEST",
        "catalog_scope": "medical_health_national",
        "title": "部门政策目录测试",
        "permission_scope": "internal_company_reference",
        "source_file": {"file_name": "policy.xlsx", "sha256": "a" * 64},
        "worksheet_name": "政策",
        "records": records,
        "built_at": "2026-08-14T00:00:00+08:00",
        "content_hash": "b" * 64,
    }


class PolicyCatalogLocalTests(unittest.TestCase):
    def test_project_snapshot_selects_catalog_instead_of_global_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="POLICY-CATALOG-SNAPSHOT"
            )
            database = Path(initialized["database"])
            first = catalog_payload()
            second = json.loads(json.dumps(first, ensure_ascii=False))
            second["catalog_id"] = "POLICYCATALOG-OTHER-SCOPE"
            second["catalog_scope"] = "other_policy_scope"
            second["content_hash"] = "c" * 64
            for index, record in enumerate(second["records"], 1):
                record["catalog_entry_id"] = f"POLICYCATENTRY-OTHER-{index}"
                record["row_hash"] = f"other-row-hash-{index}"
            import_catalog(database, first)
            import_catalog(database, second)
            record_snapshot(
                database,
                "POLICY-CATALOG-SNAPSHOT",
                source_type="policy_catalog",
                source_id=first["catalog_id"],
                content_hash=first["content_hash"],
                server_schema="medical_report_kb",
                items=[
                    (
                        "policy_catalog_entry",
                        record["catalog_entry_id"],
                        record["row_hash"],
                        record,
                    )
                    for record in first["records"]
                ],
                metadata={"records": len(first["records"])},
            )
            with connect(database) as connection:
                project = connection.execute(
                    "SELECT * FROM project WHERE project_code='POLICY-CATALOG-SNAPSHOT'"
                ).fetchone()
                selected, source = select_project_catalog(connection, project)
            self.assertEqual(selected["catalog_id"], first["catalog_id"])
            self.assertEqual(source, "project_snapshot")

    def test_duplicate_source_indexes_are_preserved_and_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="POLICY-CATALOG-DUPLICATE"
            )
            database = Path(initialized["database"])
            payload = catalog_payload()
            payload["records"][1]["source_index_no"] = payload["records"][0]["source_index_no"]
            payload["records"][1]["catalog_entry_id"] = payload["records"][0]["catalog_entry_id"]
            result = import_catalog(database, payload)
            self.assertEqual(result["records_available"], 4)
            self.assertEqual(result["duplicate_index_groups"], 1)
            with connect(database) as connection:
                rows = connection.execute(
                    """
                    SELECT catalog_entry_id,index_occurrence,index_conflict
                    FROM policy_catalog_entry
                    WHERE source_index_no=? ORDER BY index_occurrence
                    """,
                    (payload["records"][0]["source_index_no"],),
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["index_occurrence"] for row in rows], [1, 2])
            self.assertTrue(all(row["index_conflict"] == 1 for row in rows))
            self.assertEqual(len({row["catalog_entry_id"] for row in rows}), 2)

    def test_import_match_and_working_material_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-CATALOG-001",
                official_name="测试医院信息化项目",
            )
            database = Path(initialized["database"])
            payload = catalog_payload()
            first_import = import_catalog(database, payload)
            second_import = import_catalog(database, payload)
            self.assertEqual(first_import["records_available"], 4)
            self.assertEqual(first_import, second_import)

            with connect(database) as connection:
                connection.execute(
                    "UPDATE policy_catalog_entry SET verification_status='partially_verified' WHERE catalog_entry_id=?",
                    (payload["records"][0]["catalog_entry_id"],),
                )
                connection.commit()
            import_catalog(database, payload)
            with connect(database) as connection:
                status = connection.execute(
                    """
                    SELECT verification_status,jurisdiction_level,jurisdiction_code,jurisdiction_name
                    FROM policy_catalog_entry WHERE catalog_entry_id=?
                    """,
                    (payload["records"][0]["catalog_entry_id"],),
                ).fetchone()
            self.assertEqual(status["verification_status"], "partially_verified")
            self.assertEqual(
                (status["jurisdiction_level"], status["jurisdiction_code"], status["jurisdiction_name"]),
                ("national", "100000", "全国"),
            )

            topics = {"hospital_informationization", "electronic_medical_record"}
            first_match = match_candidates(database, "POLICY-CATALOG-001", topics=topics)
            second_match = match_candidates(database, "POLICY-CATALOG-001", topics=topics)
            self.assertEqual(first_match["catalog_match_run_id"], second_match["catalog_match_run_id"])
            self.assertEqual(first_match["candidate_count"], 4)
            self.assertEqual(
                {item["basis_group"] for item in first_match["candidates"]},
                {"policy", "standard"},
            )

            policy_seed = json.loads(
                (SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "core_policy_seed_20260804.json").read_text(
                    encoding="utf-8"
                )
            )
            ingest_policies(database, policy_seed)
            seed_topics = {
                topic
                for policy in policy_seed["policies"]
                for clause in policy["clauses"]
                for topic in clause.get("topic_tags", [])
            }
            match_project_policies(database, "POLICY-CATALOG-001", seed_topics, None, None)
            with connect(database) as connection:
                connection.execute(
                    "UPDATE project_policy_match SET decision_status='user_confirmed'"
                )
                connection.commit()

            working = build_material(database, "POLICY-CATALOG-001", mode="working")
            delivery = build_material(database, "POLICY-CATALOG-001", mode="delivery")
            self.assertEqual(len(working["catalog_candidates"]), 4)
            self.assertEqual(delivery["catalog_candidates"], [])
            self.assertIn("不得直接生成政策要求", material_markdown(working))

            build_composition_plan(database, "POLICY-CATALOG-001")
            task_dir = Path(tmp) / "tasks"
            export_packages(database, "POLICY-CATALOG-001", task_dir)
            policy_package = json.loads(
                next(task_dir.glob("CH1.2.1-*.json")).read_text(encoding="utf-8")
            )
            candidate_sources = [
                source
                for source in policy_package["sources"]
                if source["source_type"] == "reference"
            ]
            self.assertTrue(candidate_sources)
            self.assertTrue(
                all(source["usage_mode"] == "structure_only" for source in candidate_sources)
            )
            self.assertTrue(
                all(source["data"]["candidate_only"] for source in candidate_sources)
            )

    def test_rerun_preserves_catalog_candidate_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project", project_code="POLICY-CATALOG-DECISION"
            )
            database = Path(initialized["database"])
            import_catalog(database, catalog_payload())
            first = match_candidates(database, "POLICY-CATALOG-DECISION")
            excluded_id = first["candidates"][0]["candidate_match_id"]
            with connect(database) as connection:
                connection.execute(
                    "UPDATE project_policy_catalog_match SET decision_status='user_excluded' WHERE candidate_match_id=?",
                    (excluded_id,),
                )
                connection.commit()
            second = match_candidates(database, "POLICY-CATALOG-DECISION")
            self.assertEqual(second["catalog_match_run_id"], first["catalog_match_run_id"])
            self.assertEqual(second["candidate_count"], first["candidate_count"] - 1)

    def test_jurisdiction_change_creates_new_run_without_old_local_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            initialized = initialize_project(
                Path(tmp) / "project",
                project_code="POLICY-CATALOG-JURISDICTION-CHANGE",
                jurisdiction_code="540400",
                jurisdiction_name="林芝市",
            )
            database = Path(initialized["database"])
            payload = catalog_payload()
            payload["records"][0].update(
                {
                    "jurisdiction_level": "province",
                    "jurisdiction_code": "540000",
                    "jurisdiction_name": "西藏自治区",
                }
            )
            payload["records"][1].update(
                {
                    "jurisdiction_level": "province",
                    "jurisdiction_code": "510000",
                    "jurisdiction_name": "四川省",
                }
            )
            import_catalog(database, payload)

            first = match_candidates(database, "POLICY-CATALOG-JURISDICTION-CHANGE")
            first_titles = {item["title"] for item in first["candidates"]}
            self.assertIn(payload["records"][0]["title"], first_titles)
            self.assertNotIn(payload["records"][1]["title"], first_titles)

            with connect(database) as connection:
                connection.execute(
                    "UPDATE project SET jurisdiction_code='510100',jurisdiction_name='成都市'"
                )
                connection.commit()

            second = match_candidates(database, "POLICY-CATALOG-JURISDICTION-CHANGE")
            second_titles = {item["title"] for item in second["candidates"]}
            self.assertNotEqual(first["catalog_match_run_id"], second["catalog_match_run_id"])
            self.assertNotEqual(first["match_input_signature"], second["match_input_signature"])
            self.assertIn(payload["records"][1]["title"], second_titles)
            self.assertNotIn(payload["records"][0]["title"], second_titles)


if __name__ == "__main__":
    unittest.main()
