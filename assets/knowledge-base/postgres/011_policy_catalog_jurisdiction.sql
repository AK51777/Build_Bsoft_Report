ALTER TABLE medical_report_kb.policy_catalog_entry
  ADD COLUMN IF NOT EXISTS jurisdiction_level TEXT NOT NULL DEFAULT 'unclassified'
    CHECK (jurisdiction_level IN ('national','province','prefecture','county','unclassified')),
  ADD COLUMN IF NOT EXISTS jurisdiction_code TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS jurisdiction_name TEXT NOT NULL DEFAULT '';

UPDATE medical_report_kb.policy_catalog_entry
SET jurisdiction_level='national',jurisdiction_code='100000',jurisdiction_name='全国'
WHERE jurisdiction_level='unclassified'
  AND authority_level_label IN ('国家','国家级','中央','国务院','部委','司局');

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_jurisdiction
  ON medical_report_kb.policy_catalog_entry(
    catalog_id,jurisdiction_level,jurisdiction_code,entry_status
  );

CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_catalog_entry AS
SELECT entry.*
FROM medical_report_kb.policy_catalog_entry AS entry
JOIN medical_report_kb.policy_catalog AS catalog ON catalog.catalog_id = entry.catalog_id
WHERE catalog.catalog_status = 'published'
  AND entry.entry_status = 'active';
