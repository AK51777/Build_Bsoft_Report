from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from capture_policy_sources_postgres import (  # noqa: E402
    HostRateLimiter,
    capture_entry,
    classify_entry,
    normalize_source_url,
)


class FakeResponse:
    def __init__(self, body: bytes, *, url: str, content_type: str = "text/html; charset=utf-8"):
        self.body = body
        self.url = url
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, size: int):
        return self.body[:size]


def entry(**overrides):
    value = {
        "catalog_entry_id": "POLICYCATENTRY-test",
        "catalog_group_code": "CN1",
        "authority_level_label": "国家",
        "title": "《测试政策》",
        "document_no": "国测〔2026〕1号",
        "issuer": "测试部委",
        "notes": "",
        "external_url": "https://www.gov.cn/policy/test.html",
    }
    value.update(overrides)
    return value


class PolicySourceCaptureTests(unittest.TestCase):
    def test_normalize_source_url_accepts_bare_domain_and_rejects_notes(self):
        self.assertEqual(
            normalize_source_url("nhc.gov.cn/example/policy.shtml"),
            "https://nhc.gov.cn/example/policy.shtml",
        )
        self.assertEqual(normalize_source_url("(暂无，建议参考本地文件)"), "")

    def test_classification_isolated_from_formal_candidates(self):
        self.assertEqual(classify_entry(entry(catalog_group_code="CNX")), "reference_only")
        self.assertEqual(
            classify_entry(entry(notes="目前仍处于征求意见稿阶段")),
            "draft_or_internal",
        )
        self.assertEqual(classify_entry(entry()), "formal_candidate")

    def test_html_capture_extracts_text_attachments_and_identity(self):
        html = (
            "<html><head><title>测试政策</title><script>ignore me</script></head>"
            "<body><h1>《测试政策》</h1><p>国测〔2026〕1号</p>"
            "<p>这是政策正文。" + "用于验证正文采集。" * 30 + "</p>"
            "<a href='/files/attachment.pdf'>附件</a></body></html>"
        ).encode("utf-8")

        def opener(request, timeout):
            return FakeResponse(html, url=request.full_url)

        result = capture_entry(
            entry(),
            timeout=1,
            retries=0,
            max_bytes=1024 * 1024,
            limiter=HostRateLimiter(0),
            opener=opener,
        )
        self.assertEqual(result["retrieval_status"], "fetched_html")
        self.assertEqual(result["content_readiness"], "text_ready")
        self.assertEqual(result["identity_status"], "matched")
        self.assertNotIn("ignore me", result["extracted_text"])
        self.assertEqual(
            result["attachment_urls"],
            ["https://www.gov.cn/files/attachment.pdf"],
        )
        self.assertTrue(result["raw_sha256"])
        self.assertTrue(result["extracted_text_sha256"])

    def test_missing_url_creates_reviewable_state_without_fetch(self):
        result = capture_entry(
            entry(external_url=""),
            timeout=1,
            retries=0,
            max_bytes=1024,
            limiter=HostRateLimiter(0),
            opener=lambda *_args, **_kwargs: self.fail("opener must not be called"),
        )
        self.assertEqual(result["retrieval_status"], "missing_url")
        self.assertEqual(result["content_readiness"], "manual_source_required")
        self.assertEqual(result["verification_status"], "unverified")

    def test_412_is_kept_for_url_revalidation_and_never_verified(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 412, "Precondition Failed", {}, io.BytesIO())

        result = capture_entry(
            entry(external_url="https://www.nhc.gov.cn/old.shtml"),
            timeout=1,
            retries=0,
            max_bytes=1024,
            limiter=HostRateLimiter(0),
            opener=opener,
        )
        self.assertEqual(result["retrieval_status"], "http_error")
        self.assertEqual(result["content_readiness"], "url_revalidation_required")
        self.assertEqual(result["http_status"], 412)
        self.assertEqual(result["verification_status"], "unverified")

    def test_migration_keeps_capture_out_of_formal_runtime_view(self):
        sql = (ROOT / "assets" / "knowledge-base" / "postgres" / "012_policy_source_capture.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("policy_source_capture", sql)
        self.assertIn("review_policy_source_capture_latest", sql)
        self.assertNotIn("CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_clause", sql)
        self.assertIn("never enter runtime policy evidence views automatically", sql)


if __name__ == "__main__":
    unittest.main()
