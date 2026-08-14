CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_catalog_entry AS
SELECT entry.*
FROM medical_report_kb.policy_catalog_entry AS entry
JOIN medical_report_kb.policy_catalog AS catalog ON catalog.catalog_id = entry.catalog_id
WHERE catalog.catalog_status = 'published'
  AND entry.entry_status = 'active';
