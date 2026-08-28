from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from construction_alignment import (  # noqa: E402
    PENDING_MARKER,
    PENDING_CONFIRMATION_MARKER,
    _candidate_sort_key,
    apply_decisions,
    assemble,
    capture_scope_snapshot,
    match_scope,
    validate_manifest,
)
from ingest_scope_items_sqlite import ingest_scope_payload  # noqa: E402
from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text, upsert_project  # noqa: E402


class ConstructionAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "project.sqlite"
        with connect(self.database) as conn:
            apply_migrations(conn)
            upsert_project(
                conn,
                {
                    "project_code": "ALIGN-001",
                    "official_name": "模块级对照测试项目",
                },
            )
            conn.commit()
        self.scope_payload = {
            "source": {
                "path": str(Path(self.tempdir.name) / "客户建设清单.xlsx"),
                "sha256": "scope-source-hash",
                "size_bytes": 100,
            },
            "sheet_count": 1,
            "sheets": [
                {
                    "name": "软件清单",
                    "headers": ["序号", "软件大类", "软件系统名称", "模块名称"],
                    "detected_columns": {
                        "original_name": ["软件系统名称", "模块名称"],
                        "domain": ["软件大类"],
                    },
                    "merged_ranges": ["B2:B4", "C2:C3"],
                    "rows": [
                        {
                            "_source_row": 2,
                            "序号": "1",
                            "软件大类": "院内集成平台及数据中心",
                            "软件系统名称": "医院信息基础平台",
                            "模块名称": "主数据管理",
                        },
                        {
                            "_source_row": 3,
                            "序号": "2",
                            "软件大类": "院内集成平台及数据中心",
                            "软件系统名称": "医院信息基础平台",
                            "模块名称": "患者主索引系统",
                        },
                        {
                            "_source_row": 4,
                            "序号": "3",
                            "软件大类": "院内集成平台及数据中心",
                            "软件系统名称": "医院信息基础平台",
                            "模块名称": "院内新增专用模块",
                        },
                    ],
                }
            ],
        }
        ingest_scope_payload(self.database, self.scope_payload, project_code="ALIGN-001")
        self._seed_standard_knowledge()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed_standard_knowledge(self) -> None:
        timestamp = now_iso()
        blocks = [
            (
                "BLOCK-MDM-ROOT",
                ["建设内容", "医院信息平台", "医院信息基础平台", "主数据管理"],
                "主数据管理标准总述，必须原文导入。",
                "paragraph:100",
                100,
            ),
            (
                "BLOCK-MDM-CHILD",
                ["建设内容", "医院信息平台", "医院信息基础平台", "主数据管理", "基础数据管理"],
                "基础数据管理全部子内容，不能改写。",
                "paragraph:101",
                101,
            ),
            (
                "BLOCK-EMPI",
                ["建设内容", "医院信息平台", "医院信息基础平台", "患者主索引"],
                "患者主索引标准方案原文。",
                "paragraph:110",
                110,
            ),
            (
                "BLOCK-POLLUTION",
                ["建设内容", "智慧管理", "耗材管理系统", "主数据管理"],
                "这是同名但属于耗材管理的错误内容。",
                "paragraph:900",
                900,
            ),
            (
                "BLOCK-MESSAGE-ALIAS",
                ["建设内容", "医院信息平台", "医院信息基础平台", "统一消息中心"],
                "统一消息中心标准方案原文。",
                "paragraph:120",
                120,
            ),
            (
                "BLOCK-INTEGRATION-CLINICAL",
                ["建设内容", "医院信息平台", "医院信息集成平台", "临床业务服务集成"],
                "临床业务服务集成标准方案原文。",
                "paragraph:130",
                130,
            ),
            (
                "BLOCK-INTEGRATION-MEDICAL",
                ["建设内容", "医院信息平台", "医院信息集成平台", "医疗管理服务集成"],
                "医疗管理服务集成标准方案原文。",
                "paragraph:131",
                131,
            ),
            (
                "BLOCK-INTEGRATION-OPERATION",
                ["建设内容", "医院信息平台", "医院信息集成平台", "运营管理服务集成"],
                "运营管理服务集成标准方案原文。",
                "paragraph:132",
                132,
            ),
            (
                "BLOCK-INTEGRATION-PATIENT",
                ["建设内容", "医院信息平台", "医院信息集成平台", "患者公众服务集成"],
                "患者公众服务集成标准方案原文。",
                "paragraph:133",
                133,
            ),
            (
                "BLOCK-CDR",
                ["建设内容", "医院信息平台", "医院数据中心", "临床数据中心(CDR)"],
                "临床数据中心标准方案原文。",
                "paragraph:140",
                140,
            ),
            (
                "BLOCK-DATA-QUALITY",
                ["建设内容", "医院信息平台", "数据治理平台", "数据质量管理"],
                "数据质量管理标准方案原文。",
                "paragraph:141",
                141,
            ),
        ]
        with connect(self.database) as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO source_document (
                  source_id,project_id,source_scope,source_class,file_name,file_type,
                  source_path,sha256,usage_scope,verification_status,imported_at
                ) VALUES ('SOURCE-STANDARD',NULL,'shared','standard_solution','标准方案.docx',
                  'DOCX','server://standard','standard-source-hash','standard_solution_reuse',
                  'verified',?)
                """,
                (timestamp,),
            )
            conn.execute(
                """
                INSERT INTO corpus_document (
                  corpus_document_id,source_id,document_type,project_type,quality_level,
                  permission_scope,review_status,version,created_at,updated_at,source_corpus_type
                ) VALUES ('CORPUS-STANDARD','SOURCE-STANDARD','feasibility_study',
                  'hospital_informationization','A','company_private','approved','1',?,?,
                  'standard_solution')
                """,
                (timestamp, timestamp),
            )
            for block_id, heading_path, text, location, source_order in blocks:
                conn.execute(
                    """
                    INSERT INTO corpus_block (
                      block_id,corpus_document_id,source_location,section_role,module_code,
                      clean_text,reuse_class,quality_level,review_status,text_hash,
                      created_at,updated_at,heading_path_json,content_type,semantic_section,
                      content_slot,source_order,adaptation_mode
                    ) VALUES (?,'CORPUS-STANDARD',?,'construction_content','',?,'A','A',
                      'approved',?,?,?,?, 'construction_solution','application_software_solution',
                      '',?,'direct')
                    """,
                    (
                        block_id,
                        location,
                        text,
                        sha256_text(text),
                        timestamp,
                        timestamp,
                        dump_json(heading_path),
                        source_order,
                    ),
                )
            capabilities = [
                (
                    "CAP-MDM",
                    "医院信息基础平台",
                    "主数据管理",
                    ["BLOCK-MDM-ROOT", "BLOCK-MDM-CHILD", "BLOCK-POLLUTION"],
                ),
                (
                    "CAP-EMPI",
                    "医院信息基础平台",
                    "患者主索引",
                    ["BLOCK-EMPI"],
                ),
                (
                    "CAP-MESSAGE",
                    "医院信息基础平台",
                    "统一消息平台",
                    ["BLOCK-MESSAGE-ALIAS"],
                ),
                (
                    "CAP-INTEGRATION-CLINICAL",
                    "医院信息集成平台软件",
                    "临床服务系统整合",
                    ["BLOCK-INTEGRATION-CLINICAL"],
                ),
                (
                    "CAP-INTEGRATION-MEDICAL",
                    "医院信息集成平台软件",
                    "医疗管理系统整合",
                    ["BLOCK-INTEGRATION-MEDICAL"],
                ),
                (
                    "CAP-INTEGRATION-OPERATION",
                    "医院信息集成平台软件",
                    "运营管理系统整合",
                    ["BLOCK-INTEGRATION-OPERATION"],
                ),
                (
                    "CAP-INTEGRATION-PATIENT",
                    "医院信息集成平台软件",
                    "患者服务系统整合",
                    ["BLOCK-INTEGRATION-PATIENT"],
                ),
                (
                    "CAP-CDR",
                    "医院数据中心",
                    "临床数据中心(CDR)",
                    ["BLOCK-CDR"],
                ),
                (
                    "CAP-DATA-QUALITY",
                    "数据治理平台",
                    "数据质量管理",
                    ["BLOCK-DATA-QUALITY"],
                ),
            ]
            for capability_id, product_name, module_name, block_ids in capabilities:
                conn.execute(
                    """
                    INSERT INTO product_capability (
                      capability_id,product_code,product_name,capability_name,
                      capability_description,standard_block_ids_json,review_status,
                      category,module_name,block_match_scope
                    ) VALUES (?,?,?,?,?,?,'approved','医院平台',?,'capability_or_module_heading')
                    """,
                    (
                        capability_id,
                        f"PRODUCT-{capability_id}",
                        product_name,
                        module_name,
                        f"{product_name} / {module_name}",
                        dump_json(block_ids),
                        module_name,
                    ),
                )
            conn.commit()

    def test_module_match_review_complete_subtree_and_gap(self) -> None:
        captured = capture_scope_snapshot(self.database, "ALIGN-001", self.scope_payload)
        self.assertEqual(captured["scope_row_count"], 3)
        matched = match_scope(self.database, "ALIGN-001")
        self.assertEqual(matched["summary"]["auto_confirmed_exact"], 1)
        self.assertEqual(matched["summary"]["needs_human_review"], 1)
        self.assertEqual(matched["summary"]["content_missing"], 1)

        mdm = matched["items"][0]
        selected = next(
            candidate
            for candidate in mdm["candidates"]
            if candidate["candidate_id"] == mdm["auto_selected_candidate_id"]
        )
        self.assertEqual(
            selected["subtree_block_ids"], ["BLOCK-MDM-ROOT", "BLOCK-MDM-CHILD"]
        )
        self.assertNotIn("BLOCK-POLLUTION", selected["subtree_block_ids"])

        similar = matched["items"][1]
        self.assertEqual(similar["state"], "needs_human_review")
        similar_candidate = next(
            candidate for candidate in similar["candidates"] if candidate["capability_id"] == "CAP-EMPI"
        )
        applied = apply_decisions(
            self.database,
            {
                "match_run_id": matched["match_run_id"],
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-23T10:00:00+08:00",
                "decisions": [
                    {
                        "scope_row_id": similar["scope_row_id"],
                        "candidate_id": similar_candidate["candidate_id"],
                        "decision": "confirmed",
                        "decision_note": "确认患者主索引系统对应患者主索引。",
                    },
                    {
                        "scope_row_id": matched["items"][2]["scope_row_id"],
                        "decision": "confirmed_gap",
                        "decision_note": "标准库暂无该模块，保留待补充。",
                    },
                ],
            },
        )
        self.assertEqual(applied["applied"], 2)

        manifest, markdown = assemble(
            self.database, "ALIGN-001", matched["match_run_id"]
        )
        self.assertEqual(manifest["status"], "with_pending_supplement")
        self.assertEqual(
            [item["source_ordinal"] for item in manifest["application_software_solution"]["items"]],
            [1, 2, 3],
        )
        first = manifest["application_software_solution"]["items"][0]
        self.assertEqual(first["block_ids"], ["BLOCK-MDM-ROOT", "BLOCK-MDM-CHILD"])
        self.assertEqual(
            [fragment["clean_text"] for fragment in first["fragments"]],
            ["主数据管理标准总述，必须原文导入。", "基础数据管理全部子内容，不能改写。"],
        )
        self.assertIn(PENDING_MARKER, markdown)
        self.assertIn("|1|院内集成平台及数据中心|医院信息基础平台|主数据管理|", markdown)
        validation = validate_manifest(self.database, manifest)
        self.assertTrue(validation["valid"], validation)

        tampered = copy.deepcopy(manifest)
        tampered["application_software_solution"]["items"][0]["fragments"][0]["clean_text"] += "改写"
        tampered_validation = validate_manifest(self.database, tampered)
        self.assertFalse(tampered_validation["valid"])
        self.assertIn(
            "standard_text_not_verbatim",
            {issue["code"] for issue in tampered_validation["issues"]},
        )

    def test_exact_module_name_is_not_hidden_by_ready_similar_candidates(self) -> None:
        candidates = [
            {
                "capability": {"capability_id": f"CAP-SIMILAR-{index}"},
                "module_name_exact": False,
                "hierarchy_exact": True,
                "candidate_status": "ready",
                "name_score": 0.98 - index / 100,
                "parent_score": 1.0,
            }
            for index in range(6)
        ]
        candidates.append(
            {
                "capability": {"capability_id": "CAP-EXACT-ROOT-MISSING"},
                "module_name_exact": True,
                "hierarchy_exact": True,
                "candidate_status": "blocked",
                "name_score": 1.0,
                "parent_score": 1.0,
            }
        )

        candidates.sort(key=_candidate_sort_key, reverse=True)

        self.assertEqual(
            candidates[0]["capability"]["capability_id"],
            "CAP-EXACT-ROOT-MISSING",
        )

    def test_database_capability_aliases_recall_unique_solution_roots_for_review(self) -> None:
        payload = copy.deepcopy(self.scope_payload)
        payload["source"]["sha256"] = "scope-integration-aliases"
        payload["sheets"][0]["merged_ranges"] = []
        aliases = [
            ("临床服务系统整合", "临床业务服务集成"),
            ("医疗管理系统整合", "医疗管理服务集成"),
            ("运营管理系统整合", "运营管理服务集成"),
            ("患者服务系统整合", "患者公众服务集成"),
        ]
        payload["sheets"][0]["rows"] = [
            {
                "_source_row": index + 2,
                "序号": str(index + 1),
                "软件大类": "院内集成平台及数据中心",
                "软件系统名称": "医院信息集成平台软件",
                "模块名称": scope_name,
            }
            for index, (scope_name, _) in enumerate(aliases)
        ]
        ingest_scope_payload(self.database, payload, project_code="ALIGN-001")
        capture_scope_snapshot(self.database, "ALIGN-001", payload)

        matched = match_scope(self.database, "ALIGN-001")

        self.assertEqual(matched["summary"]["content_missing"], 0)
        self.assertEqual(matched["summary"]["needs_human_review"], 4)
        for item, (_, expected_root) in zip(matched["items"], aliases):
            candidate = item["candidates"][0]
            self.assertEqual(candidate["root_heading_path"][-1], expected_root)
            self.assertEqual(candidate["root_match_type"], "module_only")
            self.assertEqual(candidate["candidate_status"], "needs_review")
            self.assertFalse(item["auto_selected_candidate_id"])

    def test_assembly_restores_customer_categories_and_database_parent_headings(self) -> None:
        payload = copy.deepcopy(self.scope_payload)
        payload["source"]["sha256"] = "scope-heading-ancestors"
        payload["sheets"][0]["merged_ranges"] = []
        payload["sheets"][0]["rows"] = [
            {
                "_source_row": 2,
                "序号": "1",
                "软件大类": "院内集成平台及数据中心",
                "软件系统名称": "医院数据中心",
                "模块名称": "临床数据中心(CDR)",
            },
            {
                "_source_row": 3,
                "序号": "2",
                "软件大类": "院内集成平台及数据中心",
                "软件系统名称": "数据治理平台",
                "模块名称": "数据质量管理",
            },
            {
                "_source_row": 4,
                "序号": "3",
                "软件大类": "HIS系统",
                "软件系统名称": "医院信息基础平台",
                "模块名称": "主数据管理",
            },
            {
                "_source_row": 5,
                "序号": "4",
                "软件大类": "医技业务",
                "软件系统名称": "医院信息基础平台",
                "模块名称": "主数据管理",
            },
        ]
        ingest_scope_payload(self.database, payload, project_code="ALIGN-001")
        capture_scope_snapshot(self.database, "ALIGN-001", payload)
        matched = match_scope(self.database, "ALIGN-001")
        self.assertEqual(matched["summary"]["auto_confirmed_exact"], 4)

        manifest, markdown = assemble(self.database, "ALIGN-001", matched["match_run_id"])

        self.assertEqual(markdown.count("### 院内集成平台及数据中心\n"), 1)
        self.assertEqual(markdown.count("### HIS系统\n"), 1)
        self.assertEqual(markdown.count("### 医技业务\n"), 1)
        self.assertIn("#### 医院数据中心\n", markdown)
        self.assertIn("##### 临床数据中心(CDR)\n", markdown)
        self.assertIn("#### 数据治理平台\n", markdown)
        self.assertIn("##### 数据质量管理\n", markdown)
        self.assertEqual(markdown.count("#### 医院信息基础平台\n"), 2)
        self.assertTrue(validate_manifest(self.database, manifest)["valid"])

    def test_human_can_bind_exact_capability_to_reviewed_alias_root(self) -> None:
        payload = copy.deepcopy(self.scope_payload)
        payload["source"]["sha256"] = "scope-message-alias"
        payload["sheets"][0]["merged_ranges"] = []
        payload["sheets"][0]["rows"] = [
            {
                "_source_row": 2,
                "序号": "1",
                "软件大类": "院内集成平台及数据中心",
                "软件系统名称": "医院信息基础平台",
                "模块名称": "统一消息平台",
            }
        ]
        ingest_scope_payload(self.database, payload, project_code="ALIGN-001")
        capture_scope_snapshot(self.database, "ALIGN-001", payload)
        matched = match_scope(self.database, "ALIGN-001")
        item = matched["items"][0]
        candidate = next(
            value for value in item["candidates"] if value["capability_id"] == "CAP-MESSAGE"
        )
        self.assertEqual(candidate["root_match_type"], "missing")

        preview, preview_markdown = assemble(
            self.database,
            "ALIGN-001",
            matched["match_run_id"],
            allow_unresolved_preview=True,
        )
        self.assertEqual(preview["status"], "blocked")
        self.assertTrue(preview["preview_only"])
        self.assertIn(PENDING_CONFIRMATION_MARKER, preview_markdown)
        self.assertFalse(validate_manifest(self.database, preview)["valid"])

        applied = apply_decisions(
            self.database,
            {
                "match_run_id": matched["match_run_id"],
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-24T11:00:00+08:00",
                "decisions": [
                    {
                        "scope_row_id": item["scope_row_id"],
                        "candidate_id": candidate["candidate_id"],
                        "root_heading_path": [
                            "建设内容",
                            "医院信息平台",
                            "医院信息基础平台",
                            "统一消息中心",
                        ],
                        "decision": "confirmed",
                        "decision_note": "人工确认模块标题别名。",
                    }
                ],
            },
        )
        self.assertEqual(applied["applied"], 1)
        manifest, _ = assemble(self.database, "ALIGN-001", matched["match_run_id"])
        assembled = manifest["application_software_solution"]["items"][0]
        self.assertEqual(assembled["block_ids"], ["BLOCK-MESSAGE-ALIAS"])
        self.assertEqual(
            assembled["root_heading_path"][-1],
            "统一消息中心",
        )


if __name__ == "__main__":
    unittest.main()
