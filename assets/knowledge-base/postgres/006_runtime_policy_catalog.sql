CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_catalog AS
SELECT
  catalog_id,
  schema_version,
  catalog_scope,
  title,
  source_file_name,
  source_sha256,
  worksheet_name,
  permission_scope,
  record_count,
  content_hash,
  metadata,
  published_at
FROM medical_report_kb.policy_catalog
WHERE catalog_status = 'published';
