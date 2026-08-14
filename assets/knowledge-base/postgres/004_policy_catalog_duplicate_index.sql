ALTER TABLE medical_report_kb.policy_catalog_entry
  ADD COLUMN IF NOT EXISTS index_occurrence INTEGER NOT NULL DEFAULT 1
    CHECK (index_occurrence > 0),
  ADD COLUMN IF NOT EXISTS index_conflict BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE medical_report_kb.policy_catalog_entry
  DROP CONSTRAINT IF EXISTS policy_catalog_entry_catalog_id_source_index_no_key;

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_source_index
  ON medical_report_kb.policy_catalog_entry(catalog_id, source_index_no, source_row);
