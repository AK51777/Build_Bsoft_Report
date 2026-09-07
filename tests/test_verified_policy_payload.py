from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"), str(Path(__file__).parent)]
from test_policy_source_review_workpack import catalog, row
from build_policy_source_review_workpack import build_workpack
from build_verified_policy_payload import compile_reviews
from knowledge_db import sha256_text
from local_knowledge_packages import build_policy_package


def fixture():
    text = "测试政策1\n测试〔2026〕1号\n测试部门\n第一条 支持医疗机构完善数据共享。"
    workpack = build_workpack(catalog(1), [row(
        1, extracted_text=text, extracted_text_sha256=sha256_text(text),
        raw_sha256="b"*64, fetched_at="2026-09-01T00:00:00+00:00",
    )], include_extracted_text=True)
    review = {
        "decision": "approve", "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00", "verified_title": "测试政策1",
        "verified_document_no": "测试〔2026〕1号", "verified_issuer": "测试部门",
        "verified_publish_date": "2026-09-01", "verified_official_url": "https://example.gov.cn/1",
        "validity_status": "current", "validity_evidence_url": "https://example.gov.cn/1",
        "review_notes": "合成测试，不代表真实政策核验。",
        "identity_confirmed": True, "official_source_confirmed": True,
        "permitted_use_confirmed": True, "validity_confirmed": True,
        "authority_group": 30, "authority_rank": 10,
        "jurisdiction": {"level": "national", "code": "100000", "name": "全国"},
        "clauses": [{"article_path": "第一条", "original_text": "支持医疗机构完善数据共享。",
                     "normalized_summary": "支持完善数据共享。", "topic_tags": ["data_governance"],
                     "requirement_type": "encouraging", "permitted_sections": ["basis", "policy_background"]}],
    }
    decisions = {key: workpack[key] for key in ("review_run_id", "input_signature")}
    decisions["records"] = [{"catalog_entry_id": "POLICYCATENTRY-1", "review": review}]
    return workpack, decisions


class VerifiedPolicyPayloadTests(unittest.TestCase):
    def test_approved_excerpts_build_a_local_package_without_production_writes(self):
        current, decisions = fixture()
        payload = compile_reviews(current, decisions)
        self.assertFalse(payload["production_write_performed"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policies.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = build_policy_package(Path(tmp) / "policy.sqlite", release_version="test", verified_policies_path=path)
        self.assertEqual(result["counts"]["verified_policy_clauses"], 1)

    def test_pending_or_incomplete_approval_cannot_be_compiled(self):
        for key, value in (("decision", "pending"), ("reviewer", ""), ("validity_confirmed", False),
                           ("validity_status", "expired"), ("clauses", [])):
            with self.subTest(key=key):
                current, decisions = fixture()
                decisions["records"][0]["review"][key] = value
                with self.assertRaises(ValueError):
                    compile_reviews(current, decisions)

    def test_stale_signature_source_changes_and_fabricated_clauses_are_rejected(self):
        current, decisions = fixture()
        changed = copy.deepcopy(current)
        changed["records"][0]["capture"]["raw_sha256"] = "c"*64
        with self.assertRaisesRegex(ValueError, "stale"):
            compile_reviews(changed, decisions)
        changed = copy.deepcopy(current)
        changed["records"][0]["capture"]["extracted_text"] += "changed"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            compile_reviews(changed, decisions)
        decisions["records"][0]["review"]["clauses"][0]["original_text"] = "凭空新增的强制要求。"
        with self.assertRaisesRegex(ValueError, "exact excerpt"):
            compile_reviews(current, decisions)

    def test_wrong_identity_new_url_and_duplicate_reviews_are_rejected(self):
        for key, value in (("verified_title", "另一文件"), ("verified_document_no", "不存在文号"),
                           ("verified_official_url", "https://example.gov.cn/replacement")):
            current, decisions = fixture()
            decisions["records"][0]["review"][key] = value
            with self.assertRaises(ValueError):
                compile_reviews(current, decisions)
        current, decisions = fixture()
        decisions["records"].append(copy.deepcopy(decisions["records"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            compile_reviews(current, decisions)


if __name__ == "__main__":
    unittest.main()
