CREATE TABLE IF NOT EXISTS policy_catalog (
  catalog_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  catalog_scope TEXT NOT NULL,
  title TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  source_file_name TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL DEFAULT '',
  worksheet_name TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  catalog_status TEXT NOT NULL DEFAULT 'active'
    CHECK (catalog_status IN ('active','superseded')),
  imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_catalog_entry (
  catalog_entry_id TEXT PRIMARY KEY,
  catalog_id TEXT NOT NULL REFERENCES policy_catalog(catalog_id) ON DELETE CASCADE,
  source_row INTEGER NOT NULL,
  source_index_no TEXT NOT NULL,
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
  UNIQUE (catalog_id, source_index_no)
);

CREATE TABLE IF NOT EXISTS policy_catalog_match_run (
  catalog_match_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  catalog_id TEXT NOT NULL REFERENCES policy_catalog(catalog_id) ON DELETE CASCADE,
  matcher_version TEXT NOT NULL,
  topic_tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_policy_catalog_match (
  candidate_match_id TEXT PRIMARY KEY,
  catalog_match_run_id TEXT NOT NULL REFERENCES policy_catalog_match_run(catalog_match_run_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  catalog_entry_id TEXT NOT NULL REFERENCES policy_catalog_entry(catalog_entry_id) ON DELETE CASCADE,
  basis_group TEXT NOT NULL CHECK (basis_group IN ('policy','standard')),
  suggested_use TEXT NOT NULL
    CHECK (suggested_use IN ('basis','background','technical','security','investment','performance')),
  relevance_level TEXT NOT NULL
    CHECK (relevance_level IN ('core','important','supplementary','not_applicable')),
  score REAL NOT NULL,
  match_reasons_json TEXT NOT NULL DEFAULT '[]',
  decision_status TEXT NOT NULL DEFAULT 'ai_recommended'
    CHECK (decision_status IN ('ai_recommended','user_confirmed','user_excluded','needs_confirmation')),
  decision_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (catalog_match_run_id, catalog_entry_id)
);

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_lookup
  ON policy_catalog_entry(catalog_id, entry_status, verification_status, catalog_group_code);

CREATE INDEX IF NOT EXISTS idx_policy_catalog_match_project
  ON policy_catalog_match_run(project_id, catalog_id, status, completed_at);

CREATE INDEX IF NOT EXISTS idx_project_policy_catalog_match_order
  ON project_policy_catalog_match(project_id, basis_group, relevance_level, score DESC);
