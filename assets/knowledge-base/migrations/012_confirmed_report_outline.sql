ALTER TABLE product_capability
  ADD COLUMN block_match_scope TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS report_outline_version (
  outline_version_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL CHECK (version_no > 0),
  source_plan_version INTEGER NOT NULL CHECK (source_plan_version > 0),
  source_signature TEXT NOT NULL,
  outline_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','superseded')),
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  confirmed_by TEXT NOT NULL DEFAULT '',
  confirmed_at TEXT,
  confirmation_note TEXT NOT NULL DEFAULT '',
  UNIQUE (project_id, version_no)
);

CREATE TABLE IF NOT EXISTS report_outline_node (
  report_outline_node_id TEXT PRIMARY KEY,
  outline_version_id TEXT NOT NULL REFERENCES report_outline_version(outline_version_id) ON DELETE CASCADE,
  parent_node_id TEXT REFERENCES report_outline_node(report_outline_node_id) ON DELETE CASCADE,
  node_code TEXT NOT NULL,
  heading_level INTEGER NOT NULL CHECK (heading_level BETWEEN 1 AND 7),
  title TEXT NOT NULL,
  node_kind TEXT NOT NULL CHECK (
    node_kind IN ('chapter','group','section','scope','capability','feature','subfeature')
  ),
  source_type TEXT NOT NULL CHECK (
    source_type IN ('blueprint','plan','scope','capability','corpus')
  ),
  source_object_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal > 0),
  applicability_status TEXT NOT NULL DEFAULT 'applicable' CHECK (
    applicability_status IN ('applicable','pending_confirmation','not_applicable')
  ),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE (outline_version_id, node_code),
  UNIQUE (outline_version_id, ordinal)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_report_outline_one_candidate
  ON report_outline_version(project_id) WHERE status='candidate';
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_outline_one_confirmed
  ON report_outline_version(project_id) WHERE status='confirmed';
CREATE INDEX IF NOT EXISTS idx_report_outline_signature
  ON report_outline_version(project_id, source_signature, status);
CREATE INDEX IF NOT EXISTS idx_report_outline_node_order
  ON report_outline_node(outline_version_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_report_outline_node_source
  ON report_outline_node(source_type, source_object_id);
