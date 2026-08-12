from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extract_document_profile import extract_profile  # noqa: E402
from export_policy_selection_data import export_selection  # noqa: E402
from apply_policy_confirmations import apply_policy_confirmations  # noqa: E402
from apply_project_confirmations import _is_placeholder, apply_confirmations  # noqa: E402
from ingest_document_standards import ingest as ingest_document_standards  # noqa: E402
from ingest_policies import ingest as ingest_policies  # noqa: E402
from ingest_project_facts import ingest as ingest_project_facts  # noqa: E402
from knowledge_db import apply_migrations, connect  # noqa: E402
from lint_docx_format import lint_docx  # noqa: E402
from match_document_standards import match as match_document_standards  # noqa: E402
from match_project_policies import match as match_project_policies  # noqa: E402
from validate_project_gates import validate  # noqa: E402


def project_payload() -> dict:
    return {
        "project": {
            "project_code": "TEST-540400",
            "official_name": "测试医院信息化建设项目",
            "document_type": "feasibility_study",
            "owner_name": "测试医院",
            "jurisdiction_code": "540400",
            "jurisdiction_name": "测试市",
            "baseline_version": "T1",
        },
        "sources": [
            {
                "source_id": "SRC-TEST-CONFIRM",
                "source_scope": "project",
                "source_class": "user_confirmation",
                "file_name": "测试确认记录",
                "file_type": "CONVERSATION",
                "verification_status": "verified",
            }
        ],
        "facts": [
            {
                "fact_id": "FACT-TEST-NAME",
                "fact_key": "project.official_name",
                "fact_category": "project_identity",
                "fact_content": "项目名称已确认。",
                "fact_status": "confirmed",
                "materiality": "A",
                "confirmation_required": False,
                "evidence": [{"source_id": "SRC-TEST-CONFIRM", "source_location": "1", "evidence_text": "名称确认", "reliability_level": "A"}],
            },
            {
                "fact_id": "FACT-TEST-SCOPE",
                "fact_key": "scope.final_boundary",
                "fact_category": "scope",
                "fact_content": "范围已确认。",
                "fact_status": "confirmed",
                "materiality": "A",
                "confirmation_required": False,
            },
            {
                "fact_id": "FACT-TEST-EMR",
                "fact_key": "acceptance.emr_level",
                "fact_category": "acceptance_target",
                "fact_content": "电子病历五级。",
                "fact_status": "confirmed",
                "materiality": "A",
                "confirmation_required": False,
            },
            {
                "fact_id": "FACT-TEST-INTEROP",
                "fact_key": "acceptance.interop_level",
                "fact_category": "acceptance_target",
                "fact_content": "互联互通四甲。",
                "fact_status": "confirmed",
                "materiality": "A",
                "confirmation_required": False,
            },
            {
                "fact_id": "FACT-TEST-INVEST",
                "fact_key": "investment.total",
                "fact_category": "investment",
                "fact_content": "总投资待补充。",
                "fact_status": "pending_supplement",
                "materiality": "A",
                "confirmation_required": True,
            },
        ],
        "questions": [
            {
                "question_id": "QUESTION-TEST-INVEST",
                "target_type": "fact",
                "target_id": "FACT-TEST-INVEST",
                "question_text": "总投资是多少？",
                "materiality": "A",
                "impact_type": "investment",
            }
        ],
        "confirmations": [
            {
                "confirmation_id": "CONFIRM-TEST-NAME",
                "target_type": "fact",
                "target_id": "FACT-TEST-NAME",
                "decision": "confirm",
                "after_value": {"status": "confirmed"},
                "confirmed_by": "测试用户",
                "confirmed_at": "2026-08-04T00:00:00+08:00",
            }
        ],
    }


def policy_payload() -> dict:
    base = {
        "issuer": "测试发布机关",
        "authority_group": 30,
        "authority_rank": 10,
        "jurisdiction_level": "national",
        "jurisdiction_code": "100000",
        "jurisdiction_name": "全国",
        "policy_type": "policy",
        "official_domain": "example.gov.cn",
        "retrieved_at": "2026-08-04T00:00:00+00:00",
        "verification_status": "verified",
    }
    return {
        "policies": [
            {
                **base,
                "policy_id": "POLICY-TEST-CURRENT",
                "title": "测试现行政策",
                "document_no": "测试〔2026〕1号",
                "publish_date": "2026-01-01",
                "validity_status": "current",
                "official_url": "https://example.gov.cn/current",
                "clauses": [
                    {
                        "clause_id": "CLAUSE-TEST-CURRENT",
                        "article_path": "第一条",
                        "original_text": "推进电子病历和互联互通建设。",
                        "normalized_summary": "推进电子病历和互联互通。",
                        "topic_tags": ["electronic_medical_record", "interoperability"],
                        "requirement_type": "guiding",
                        "verification_status": "verified",
                    }
                ],
            },
            {
                **base,
                "policy_id": "POLICY-TEST-HISTORICAL",
                "title": "测试历史规划",
                "document_no": "测试〔2021〕2号",
                "publish_date": "2021-01-01",
                "expiry_date": "2025-12-31",
                "validity_status": "historical",
                "official_url": "https://example.gov.cn/historical",
                "clauses": [
                    {
                        "clause_id": "CLAUSE-TEST-HISTORICAL",
                        "article_path": "第二条",
                        "original_text": "规划期内推进电子病历。",
                        "normalized_summary": "历史规划要求。",
                        "topic_tags": ["electronic_medical_record"],
                        "requirement_type": "background",
                        "verification_status": "verified",
                    }
                ],
            },
        ]
    }


def make_minimal_docx(path: Path) -> None:
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
</w:styles>"""
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
  <w:p><w:r><w:t>普通正文</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第一章 测试</w:t></w:r></w:p>
  <w:p><w:r><w:t xml:space="preserve"> 伪缩进</w:t></w:r></w:p>
  <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
</w:body></w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/document.xml", document)


class P0P1Tests(unittest.TestCase):
    def test_migration_hash_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            migrations = root / "migrations"
            migrations.mkdir()
            migration = migrations / "001_test.sql"
            migration.write_text("CREATE TABLE sample(id TEXT PRIMARY KEY);", encoding="utf-8")
            db = root / "test.sqlite"
            with connect(db) as conn:
                apply_migrations(conn, migrations)
                migration.write_text("CREATE TABLE sample(id INTEGER PRIMARY KEY);", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    apply_migrations(conn, migrations)

    def test_fact_confirmation_and_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            result = ingest_project_facts(db, project_payload())
            self.assertEqual(result["confirmations"], 1)
            gate = validate(db, "TEST-540400")
            # The whole project still fails because no policy evidence has
            # been loaded in this fact-only test.  Assert the fact gates
            # independently so an unrelated stage cannot hide a regression.
            self.assertEqual(gate["overall"], "fail")
            by_code = {item["gate_code"]: item["result"] for item in gate["checks"]}
            self.assertEqual(by_code["GATE-FACT-CORE"], "pass")
            self.assertEqual(by_code["GATE-FACT-PENDING"], "warning")
            self.assertEqual(by_code["GATE-POLICY-EVIDENCE"], "fail")
            with connect(db) as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE confirmation_record SET decision_note='changed'")

    def test_policy_runs_are_versioned_and_historical_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            ingest_project_facts(db, project_payload())
            ingest_policies(db, policy_payload())
            first = match_project_policies(db, "TEST-540400", {"electronic_medical_record", "interoperability"}, None, None)
            second = match_project_policies(db, "TEST-540400", {"electronic_medical_record", "interoperability"}, None, None)
            self.assertNotEqual(first["match_run_id"], second["match_run_id"])
            self.assertEqual(first["basis_policy_order"], ["POLICY-TEST-CURRENT"])
            historical = next(row for row in first["matches"] if row["policy_id"] == "POLICY-TEST-HISTORICAL")
            self.assertFalse(historical["basis_use"])
            with connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM policy_match_run").fetchone()[0], 2)

    def test_policy_gate_uses_latest_run_and_policy_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            ingest_project_facts(db, project_payload())
            ingest_policies(db, policy_payload())
            first = match_project_policies(
                db, "TEST-540400", {"electronic_medical_record", "interoperability"}, None, None
            )
            policy_decision_pack = {
                "pack_type": "policy_confirmation",
                "project_code": "TEST-540400",
                "match_run_id": first["match_run_id"],
                "decisions": [
                    {
                        "policy_id": "POLICY-TEST-CURRENT",
                        "decision": "confirm",
                        "user_order": None,
                        "decision_note": "测试确认",
                        "confirmed_by": "测试用户",
                        "confirmed_at": "2026-08-04T10:00:00+08:00",
                    }
                ],
            }
            applied = apply_policy_confirmations(db, policy_decision_pack)
            self.assertEqual(applied["decisions_applied"], 1)
            duplicate = apply_policy_confirmations(db, policy_decision_pack)
            self.assertEqual(duplicate["duplicates_ignored"], 1)
            exported = export_selection(db, "TEST-540400")
            current_row = next(
                row for row in exported["matches"] if row["policy_id"] == "POLICY-TEST-CURRENT"
            )
            self.assertEqual(current_row["decision_status"], "user_confirmed")
            gate = validate(db, "TEST-540400")
            by_code = {item["gate_code"]: item["result"] for item in gate["checks"]}
            self.assertEqual(by_code["GATE-POLICY-EVIDENCE"], "pass")
            self.assertEqual(by_code["GATE-POLICY-CONFIRMATION"], "pass")
            self.assertEqual(by_code["GATE-POLICY-CITATION-MATRIX"], "pass")

            with connect(db) as conn:
                conn.execute(
                    "UPDATE policy_document SET validity_status='historical' WHERE policy_id='POLICY-TEST-CURRENT'"
                )
                conn.commit()
            second = match_project_policies(
                db, "TEST-540400", {"electronic_medical_record", "interoperability"}, None, None
            )
            self.assertEqual(second["basis_policy_order"], [])
            with self.assertRaises(ValueError):
                apply_policy_confirmations(db, policy_decision_pack)
            gate = validate(db, "TEST-540400")
            by_code = {item["gate_code"]: item["result"] for item in gate["checks"]}
            self.assertEqual(by_code["GATE-POLICY-EVIDENCE"], "fail")

    def test_fact_confirmation_roundtrip_can_freeze_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            ingest_project_facts(db, project_payload())
            for placeholder in ("TBD", "N/A", "待定", "暂缺", "暂无", "未定"):
                self.assertTrue(_is_placeholder(placeholder))
            with self.assertRaises(ValueError):
                apply_confirmations(
                    db,
                    {
                        "pack_type": "fact_confirmation",
                        "project_code": "TEST-540400",
                        "baseline_version": "T0",
                        "decisions": [],
                        "question_answers": [],
                    },
                )
            with self.assertRaises(ValueError):
                apply_confirmations(
                    db,
                    {
                        "pack_type": "fact_confirmation",
                        "project_code": "TEST-540400",
                        "baseline_version": "T1",
                        "decisions": [
                            {
                                "target_type": "fact",
                                "target_id": "FACT-TEST-INVEST",
                                "decision": "confirm",
                                "proposed_value": "",
                                "decision_note": "不应允许确认待补充占位",
                                "confirmed_by": "测试用户",
                                "confirmed_at": "2026-08-04T10:20:00+08:00",
                            }
                        ],
                        "question_answers": [],
                    },
                )
            decision_pack = {
                "pack_type": "fact_confirmation",
                "project_code": "TEST-540400",
                "baseline_version": "T1",
                "decisions": [
                    {
                        "target_type": "fact",
                        "target_id": "FACT-TEST-INVEST",
                        "decision": "modify",
                        "proposed_value": "测试总投资为100万元。",
                        "proposed_normalized_value": "100",
                        "proposed_data_unit": "万元",
                        "proposed_statistical_date": "2026-08-04",
                        "decision_note": "测试确认待补充状态已解决",
                        "confirmed_by": "测试用户",
                        "confirmed_at": "2026-08-04T10:30:00+08:00",
                    }
                ],
                "question_answers": [],
            }
            invalid_date_pack = {
                **decision_pack,
                "decisions": [
                    {
                        **decision_pack["decisions"][0],
                        "proposed_statistical_date": "2026/08/04",
                        "confirmed_at": "2026-08-04T10:25:00+08:00",
                    }
                ],
            }
            with self.assertRaises(ValueError):
                apply_confirmations(db, invalid_date_pack)
            result = apply_confirmations(db, decision_pack, freeze=True)
            self.assertTrue(result["baseline_frozen"])
            self.assertEqual(result["unresolved_ab_items"], 0)
            duplicate = apply_confirmations(db, decision_pack, freeze=True)
            self.assertEqual(duplicate["duplicates_ignored"], 1)
            with connect(db) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT fact_status FROM project_fact WHERE fact_id='FACT-TEST-INVEST'"
                    ).fetchone()[0],
                    "confirmed",
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT normalized_value FROM project_fact WHERE fact_id='FACT-TEST-INVEST'"
                    ).fetchone()[0],
                    "100",
                )
                self.assertEqual(
                    conn.execute("SELECT status FROM project WHERE project_code='TEST-540400'").fetchone()[0],
                    "frozen",
                )
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM confirmation_record").fetchone()[0], 2)
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM fact_evidence WHERE fact_id='FACT-TEST-INVEST'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT status FROM confirmation_question WHERE question_id='QUESTION-TEST-INVEST'"
                    ).fetchone()[0],
                    "answered",
                )

    def test_document_standard_match_requires_government_investment_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            ingest_project_facts(db, project_payload())
            ingest_document_standards(
                db,
                {
                    "standards": [
                        {
                            "standard_id": "DOCSTD-TEST",
                            "document_type": "feasibility_study",
                            "jurisdiction_code": "100000",
                            "jurisdiction_name": "全国",
                            "authority_name": "测试机关",
                            "title": "政府投资项目可行性研究报告测试大纲",
                            "version": "1",
                            "verification_status": "verified",
                            "status": "active",
                        }
                    ]
                },
            )
            result = match_document_standards(db, "TEST-540400")
            self.assertEqual(len(result["matches"]), 1)
            self.assertEqual(result["matches"][0]["decision_status"], "needs_confirmation")

    def test_docx_default_style_and_whitespace_lint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "minimal.docx"
            make_minimal_docx(docx)
            extracted = extract_profile(docx)
            self.assertTrue(any(rule["semantic_role"] == "heading_1" for rule in extracted["style_contract"]["rules"]))
            report = lint_docx(docx)
            counts = report["summary"]["type_counts"]
            self.assertEqual(counts.get("unstyled_paragraph", 0), 0)
            self.assertEqual(counts.get("leading_whitespace", 0), 1)


if __name__ == "__main__":
    unittest.main()
