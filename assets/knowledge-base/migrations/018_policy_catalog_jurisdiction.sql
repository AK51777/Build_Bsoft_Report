ALTER TABLE policy_catalog_entry
  ADD COLUMN jurisdiction_level TEXT NOT NULL DEFAULT 'unclassified'
    CHECK (jurisdiction_level IN ('national','province','prefecture','county','unclassified'));

ALTER TABLE policy_catalog_entry
  ADD COLUMN jurisdiction_code TEXT NOT NULL DEFAULT '';

ALTER TABLE policy_catalog_entry
  ADD COLUMN jurisdiction_name TEXT NOT NULL DEFAULT '';

UPDATE policy_catalog_entry
SET jurisdiction_level='national',jurisdiction_code='100000',jurisdiction_name='全国'
WHERE jurisdiction_level='unclassified'
  AND authority_level_label IN ('国家','国家级','中央','国务院','部委','司局');

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_jurisdiction
  ON policy_catalog_entry(catalog_id,jurisdiction_level,jurisdiction_code,entry_status);
