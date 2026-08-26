ALTER TABLE medical_report_kb.product_capability
  ADD COLUMN IF NOT EXISTS block_match_scope TEXT NOT NULL DEFAULT '';

CREATE OR REPLACE VIEW medical_report_kb.runtime_product_capability AS
SELECT
  capability.capability_id,
  capability.package_id,
  capability.product_code,
  capability.product_name,
  capability.capability_name,
  capability.capability_description,
  capability.category,
  capability.module_name,
  capability.selection_rules,
  capability.prerequisites,
  capability.interface_dependencies,
  capability.exclusions,
  capability.applicable_versions,
  capability.source_location,
  capability.review_status,
  capability.created_at,
  capability.updated_at,
  package.content_hash AS package_content_hash,
  capability.block_match_scope
FROM medical_report_kb.product_capability AS capability
JOIN medical_report_kb.knowledge_package AS package
  ON package.package_id = capability.package_id
WHERE package.package_status = 'published'
  AND capability.review_status = 'approved';
