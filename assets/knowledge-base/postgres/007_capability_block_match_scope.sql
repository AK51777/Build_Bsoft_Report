ALTER TABLE medical_report_kb.product_capability
  ADD COLUMN IF NOT EXISTS block_match_scope TEXT NOT NULL DEFAULT '';

CREATE OR REPLACE VIEW medical_report_kb.runtime_product_capability AS
SELECT
  capability.*,
  package.content_hash AS package_content_hash
FROM medical_report_kb.product_capability AS capability
JOIN medical_report_kb.knowledge_package AS package
  ON package.package_id = capability.package_id
WHERE package.package_status = 'published'
  AND capability.review_status = 'approved';
