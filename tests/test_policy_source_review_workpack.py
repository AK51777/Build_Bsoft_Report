from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_policy_source_review_workpack import (  # noqa: E402
    build_workpack,
    render_markdown,
    review_queue,
    write_csv,
)


def catalog(record_count: int) -> dict:
    return {
        "catalog_id": "POLICYCATALOG-test",
        "catalog_status": "published",
        "title": "合成政策目录",
        "content_hash": "a" * 64,
        "permission_scope": "internal_company_reference",
        "record_count": record_count,
    }


def row(source_row: int, **overrides) -> dict:
    value = {
        "catalog_entry_id": f"POLICYCATENTRY-{source_row}",
        "source_row": source_row,
        "source_index_no": f"CN1.{source_row:03d}",
        "row_hash": f"row-hash-{source_row}",
        "entry_status": "active",
        "title": f"测试政策{source_row}",
        "document_no": f"测试〔2026〕{source_row}号",
        "issuer": "测试部门",
        "catalog_external_url": f"https://example.gov.cn/{source_row}",
        "capture_id": f"POLICYCAPTURE-{source_row}",
        "requested_url": f"https://example.gov.cn/{source_row}",
        "final_url": f"https://example.gov.cn/{source_row}",
        "source_classification": "formal_candidate",
        "retrieval_status": "fetched_html",
        "content_readiness": "text_ready",
        "identity_status": "matched",
        "http_status": 200,
        "raw_size_bytes": 1000,
        "raw_sha256": f"raw-{source_row}",
        "extracted_text_sha256": f"text-{source_row}",
        "extracted_text": "测试政策原文" * 100,
        "verification_status": "unverified",
        "review_status": "pending",
    }
    value.update(overrides)
    return value


class PolicySourceReviewWorkpackTests(unittest.TestCase):
    def test_queue_rules_keep_nonformal_and_unready_records_out_of_ready_queue(self):
        cases = [
            (row(1), "ready_identity_matched"),
            (row(2, identity_status="partial"), "review_identity_partial"),
            (row(3, identity_status="mismatch"), "review_identity_mismatch"),
            (
                row(4, retrieval_status="fetched_pdf", content_readiness="binary_pending_extraction"),
                "extract_binary",
            ),
            (
                row(5, retrieval_status="http_error", content_readiness="url_revalidation_required"),
                "revalidate_url",
            ),
            (
                row(6, retrieval_status="network_error", content_readiness="not_ready"),
                "retry_network",
            ),
            (
                row(7, retrieval_status="missing_url", content_readiness="manual_source_required"),
                "manual_source",
            ),
            (row(8, source_classification="draft_or_internal"), "excluded_nonformal"),
            (row(9, capture_id=None, content_readiness=None), "capture_missing"),
            (row(10, entry_status="retired", capture_id=None), "excluded_inactive"),
        ]
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(review_queue(value)[0], expected)

    def test_workpack_is_stable_and_never_grants_automatic_formal_eligibility(self):
        rows = [
            row(1),
            row(2, identity_status="partial"),
            row(3, source_classification="reference_only"),
        ]
        first = build_workpack(catalog(3), rows, preview_chars=20)
        second = build_workpack(catalog(3), list(reversed(rows)), preview_chars=20)
        self.assertEqual(first["review_run_id"], second["review_run_id"])
        self.assertEqual(first["input_signature"], second["input_signature"])
        self.assertEqual(first["quality_summary"]["formal_publish_eligible"], 0)
        self.assertTrue(
            all(not item["formal_publish_eligible"] for item in first["records"])
        )
        self.assertTrue(
            all(item["review"]["verification_status"] == "unverified" for item in first["records"])
        )
        self.assertTrue(
            all(item["review"]["clauses"] == [] for item in first["records"])
        )
        self.assertNotIn("extracted_text", first["records"][0]["capture"])
        self.assertEqual(len(first["records"][0]["capture"]["extracted_text_preview"]), 20)

    def test_signature_changes_when_capture_evidence_changes(self):
        before = build_workpack(catalog(1), [row(1)], preview_chars=0)
        after = build_workpack(catalog(1), [row(1, raw_sha256="changed")], preview_chars=0)
        self.assertNotEqual(before["input_signature"], after["input_signature"])
        self.assertNotEqual(before["review_run_id"], after["review_run_id"])

    def test_full_text_is_opt_in(self):
        workpack = build_workpack(
            catalog(1), [row(1)], include_extracted_text=True, preview_chars=0
        )
        self.assertEqual(workpack["records"][0]["capture"]["extracted_text"], "测试政策原文" * 100)

    def test_catalog_integrity_mismatch_is_blocking(self):
        with self.assertRaisesRegex(ValueError, "catalog row count mismatch"):
            build_workpack(catalog(2), [row(1)])
        with self.assertRaisesRegex(ValueError, "source rows"):
            build_workpack(catalog(2), [row(1), row(1, catalog_entry_id="other")])

    def test_csv_and_markdown_are_review_oriented(self):
        workpack = build_workpack(
            catalog(2),
            [row(1), row(2, source_classification="draft_or_internal")],
            preview_chars=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "review.csv"
            write_csv(workpack, csv_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["review_decision"], "")
        markdown = render_markdown(workpack, sample_per_queue=1)
        self.assertIn("正式自动发布资格：0", markdown)
        self.assertIn("ready_identity_matched", markdown)
        self.assertIn("excluded_nonformal", markdown)


if __name__ == "__main__":
    unittest.main()
