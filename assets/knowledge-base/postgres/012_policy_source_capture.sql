CREATE TABLE IF NOT EXISTS medical_report_kb.policy_source_capture (
  capture_id TEXT PRIMARY KEY,
  catalog_entry_id TEXT NOT NULL
    REFERENCES medical_report_kb.policy_catalog_entry(catalog_entry_id) ON DELETE CASCADE,
  requested_url TEXT NOT NULL DEFAULT '',
  final_url TEXT NOT NULL DEFAULT '',
  source_domain TEXT NOT NULL DEFAULT '',
  source_classification TEXT NOT NULL
    CHECK (source_classification IN ('formal_candidate','draft_or_internal','reference_only')),
  retrieval_status TEXT NOT NULL
    CHECK (retrieval_status IN (
      'missing_url','invalid_url','fetched_html','fetched_pdf','fetched_binary',
      'http_error','network_error','too_large','parse_error'
    )),
  content_readiness TEXT NOT NULL
    CHECK (content_readiness IN (
      'text_ready','binary_pending_extraction','url_revalidation_required',
      'manual_source_required','not_ready'
    )),
  identity_status TEXT NOT NULL
    CHECK (identity_status IN ('matched','partial','mismatch','not_checked')),
  http_status INTEGER,
  content_type TEXT NOT NULL DEFAULT '',
  content_charset TEXT NOT NULL DEFAULT '',
  raw_size_bytes BIGINT NOT NULL DEFAULT 0 CHECK (raw_size_bytes >= 0),
  raw_sha256 TEXT NOT NULL DEFAULT '',
  extracted_text TEXT NOT NULL DEFAULT '',
  extracted_text_sha256 TEXT NOT NULL DEFAULT '',
  extraction_method TEXT NOT NULL DEFAULT '',
  attachment_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_storage_uri TEXT NOT NULL DEFAULT '',
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('approved','pending','prohibited','retired')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (catalog_entry_id, requested_url, raw_sha256, retrieval_status)
);

CREATE INDEX IF NOT EXISTS idx_policy_source_capture_entry_time
  ON medical_report_kb.policy_source_capture(catalog_entry_id, fetched_at DESC, capture_id DESC);

CREATE INDEX IF NOT EXISTS idx_policy_source_capture_work_queue
  ON medical_report_kb.policy_source_capture(
    retrieval_status,content_readiness,identity_status,verification_status,review_status
  );

CREATE OR REPLACE VIEW medical_report_kb.review_policy_source_capture_latest AS
SELECT DISTINCT ON (capture.catalog_entry_id)
  capture.capture_id,
  capture.catalog_entry_id,
  entry.catalog_id,
  entry.source_row,
  entry.source_index_no,
  entry.title,
  entry.document_no,
  entry.issuer,
  entry.notes,
  entry.external_url AS catalog_external_url,
  capture.requested_url,
  capture.final_url,
  capture.source_domain,
  capture.source_classification,
  capture.retrieval_status,
  capture.content_readiness,
  capture.identity_status,
  capture.http_status,
  capture.content_type,
  capture.raw_size_bytes,
  capture.raw_sha256,
  capture.extracted_text_sha256,
  capture.extraction_method,
  capture.attachment_urls,
  capture.raw_storage_uri,
  capture.fetched_at,
  capture.error_code,
  capture.error_message,
  capture.verification_status,
  capture.review_status,
  capture.metadata
FROM medical_report_kb.policy_source_capture AS capture
JOIN medical_report_kb.policy_catalog_entry AS entry
  ON entry.catalog_entry_id = capture.catalog_entry_id
ORDER BY capture.catalog_entry_id,capture.fetched_at DESC,capture.capture_id DESC;

COMMENT ON TABLE medical_report_kb.policy_source_capture IS
  'Unverified policy-source capture staging. Rows never enter runtime policy evidence views automatically.';

COMMENT ON VIEW medical_report_kb.review_policy_source_capture_latest IS
  'Latest source-capture state for review and retry queues; not a formal policy runtime view.';
