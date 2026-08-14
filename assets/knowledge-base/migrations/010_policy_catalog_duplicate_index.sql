PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE policy_catalog_entry_v2 (
  catalog_entry_id TEXT PRIMARY KEY,
  catalog_id TEXT NOT NULL REFERENCES policy_catalog(catalog_id) ON DELETE CASCADE,
  source_row INTEGER NOT NULL,
  source_index_no TEXT NOT NULL,
  index_occurrence INTEGER NOT NULL DEFAULT 1 CHECK (index_occurrence > 0),
  index_conflict INTEGER NOT NULL DEFAULT 0 CHECK (index_conflict IN (0,1)),
  identity_key TEXT NOT NULL,
  catalog_group_code TEXT NOT NULL DEFAULT '',
  catalog_group_name TEXT NOT NULL DEFAULT '',
  authority_level_label TEXT NOT NULL DEFAULT '',
  category_name TEXT NOT NULL DEFAULT '',
  keyword_text TEXT NOT NULL DEFAULT '',
  keyword_tags_json TEXT NOT NULL DEFAULT '[]',
  document_no TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  publish_date TEXT NOT NULL DEFAULT '',
  publish_date_raw TEXT NOT NULL DEFAULT '',
  issuer TEXT NOT NULL DEFAULT '',
  file_count INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  external_url TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  entry_status TEXT NOT NULL DEFAULT 'active'
    CHECK (entry_status IN ('active','inactive')),
  row_hash TEXT NOT NULL,
  UNIQUE (catalog_id, source_row)
);

INSERT INTO policy_catalog_entry_v2 (
  catalog_entry_id,catalog_id,source_row,source_index_no,index_occurrence,index_conflict,
  identity_key,catalog_group_code,catalog_group_name,authority_level_label,category_name,
  keyword_text,keyword_tags_json,document_no,title,publish_date,publish_date_raw,issuer,
  file_count,notes,external_url,verification_status,entry_status,row_hash
)
SELECT
  catalog_entry_id,catalog_id,source_row,source_index_no,
  ROW_NUMBER() OVER (
    PARTITION BY catalog_id,source_index_no ORDER BY source_row,catalog_entry_id
  ),
  CASE WHEN COUNT(*) OVER (PARTITION BY catalog_id,source_index_no) > 1 THEN 1 ELSE 0 END,
  identity_key,catalog_group_code,catalog_group_name,authority_level_label,category_name,
  keyword_text,keyword_tags_json,document_no,title,publish_date,publish_date_raw,issuer,
  file_count,notes,external_url,verification_status,entry_status,row_hash
FROM policy_catalog_entry;

DROP TABLE policy_catalog_entry;
ALTER TABLE policy_catalog_entry_v2 RENAME TO policy_catalog_entry;

CREATE INDEX idx_policy_catalog_entry_lookup
  ON policy_catalog_entry(catalog_id, entry_status, verification_status, catalog_group_code);

CREATE INDEX idx_policy_catalog_entry_source_index
  ON policy_catalog_entry(catalog_id, source_index_no, source_row);

COMMIT;

PRAGMA foreign_keys = ON;
