CREATE TABLE IF NOT EXISTS construction_scope_snapshot (
  scope_snapshot_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  source_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  display_payload_json TEXT NOT NULL,
  display_hash TEXT NOT NULL,
  snapshot_status TEXT NOT NULL DEFAULT 'current'
    CHECK (snapshot_status IN ('current','superseded')),
  created_at TEXT NOT NULL,
  UNIQUE (project_id, source_sha256, display_hash)
);

CREATE TABLE IF NOT EXISTS construction_scope_row (
  scope_row_id TEXT PRIMARY KEY,
  scope_snapshot_id TEXT NOT NULL
    REFERENCES construction_scope_snapshot(scope_snapshot_id) ON DELETE CASCADE,
  scope_id TEXT REFERENCES project_scope_item(scope_id) ON DELETE SET NULL,
  source_ordinal INTEGER NOT NULL CHECK (source_ordinal > 0),
  worksheet_name TEXT NOT NULL DEFAULT '',
  source_row INTEGER,
  hierarchy_json TEXT NOT NULL DEFAULT '[]',
  display_cells_json TEXT NOT NULL DEFAULT '{}',
  display_hash TEXT NOT NULL,
  UNIQUE (scope_snapshot_id, source_ordinal)
);

CREATE TABLE IF NOT EXISTS construction_match_run (
  match_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  scope_snapshot_id TEXT NOT NULL
    REFERENCES construction_scope_snapshot(scope_snapshot_id) ON DELETE RESTRICT,
  package_id TEXT NOT NULL DEFAULT '',
  package_content_hash TEXT NOT NULL DEFAULT '',
  matcher_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('completed','blocked','superseded')),
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS construction_match_candidate (
  candidate_id TEXT PRIMARY KEY,
  match_run_id TEXT NOT NULL
    REFERENCES construction_match_run(match_run_id) ON DELETE CASCADE,
  scope_row_id TEXT NOT NULL
    REFERENCES construction_scope_row(scope_row_id) ON DELETE CASCADE,
  capability_id TEXT NOT NULL
    REFERENCES product_capability(capability_id) ON DELETE RESTRICT,
  match_class TEXT NOT NULL
    CHECK (match_class IN ('exact','similar','manual')),
  name_score REAL NOT NULL DEFAULT 0 CHECK (name_score BETWEEN 0 AND 1),
  parent_score REAL NOT NULL DEFAULT 0 CHECK (parent_score BETWEEN 0 AND 1),
  root_heading_path_json TEXT NOT NULL DEFAULT '[]',
  root_match_type TEXT NOT NULL
    CHECK (root_match_type IN ('module_and_product','module_only','ambiguous','missing')),
  subtree_block_ids_json TEXT NOT NULL DEFAULT '[]',
  candidate_status TEXT NOT NULL
    CHECK (candidate_status IN ('ready','needs_review','blocked')),
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (match_run_id, scope_row_id, capability_id, root_heading_path_json)
);

CREATE TABLE IF NOT EXISTS construction_match_decision (
  decision_id TEXT PRIMARY KEY,
  match_run_id TEXT NOT NULL
    REFERENCES construction_match_run(match_run_id) ON DELETE CASCADE,
  scope_row_id TEXT NOT NULL
    REFERENCES construction_scope_row(scope_row_id) ON DELETE CASCADE,
  candidate_id TEXT
    REFERENCES construction_match_candidate(candidate_id) ON DELETE RESTRICT,
  decision TEXT NOT NULL
    CHECK (decision IN ('confirmed','confirmed_gap','rejected','deferred')),
  decision_source TEXT NOT NULL
    CHECK (decision_source IN ('auto_exact','human')),
  chosen_capability_id TEXT
    REFERENCES product_capability(capability_id) ON DELETE RESTRICT,
  chosen_root_heading_path_json TEXT NOT NULL DEFAULT '[]',
  chosen_block_ids_json TEXT NOT NULL DEFAULT '[]',
  decision_note TEXT NOT NULL DEFAULT '',
  reviewed_by TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL,
  decision_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS construction_assembly_manifest (
  manifest_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  match_run_id TEXT NOT NULL
    REFERENCES construction_match_run(match_run_id) ON DELETE RESTRICT,
  target_section_role TEXT NOT NULL DEFAULT 'application_software_solution',
  scope_snapshot_hash TEXT NOT NULL,
  package_content_hash TEXT NOT NULL DEFAULT '',
  manifest_json TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('complete','with_pending_supplement','blocked')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS construction_assembly_item (
  manifest_id TEXT NOT NULL
    REFERENCES construction_assembly_manifest(manifest_id) ON DELETE CASCADE,
  item_order INTEGER NOT NULL CHECK (item_order > 0),
  scope_row_id TEXT NOT NULL
    REFERENCES construction_scope_row(scope_row_id) ON DELETE RESTRICT,
  capability_id TEXT
    REFERENCES product_capability(capability_id) ON DELETE RESTRICT,
  root_heading_path_json TEXT NOT NULL DEFAULT '[]',
  block_ids_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL,
  item_status TEXT NOT NULL
    CHECK (item_status IN ('verbatim','pending_supplement')),
  PRIMARY KEY (manifest_id, item_order)
);

CREATE INDEX IF NOT EXISTS idx_construction_scope_snapshot_project
  ON construction_scope_snapshot(project_id, snapshot_status, created_at);
CREATE INDEX IF NOT EXISTS idx_construction_scope_row_order
  ON construction_scope_row(scope_snapshot_id, source_ordinal);
CREATE INDEX IF NOT EXISTS idx_construction_candidate_review
  ON construction_match_candidate(match_run_id, scope_row_id, match_class, candidate_status);
CREATE INDEX IF NOT EXISTS idx_construction_decision_latest
  ON construction_match_decision(match_run_id, scope_row_id, created_at);
