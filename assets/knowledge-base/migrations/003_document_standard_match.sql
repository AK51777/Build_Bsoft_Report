PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS document_standard_match_run (
  standard_match_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  matcher_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_document_standard_match (
  standard_match_id TEXT PRIMARY KEY,
  standard_match_run_id TEXT NOT NULL REFERENCES document_standard_match_run(standard_match_run_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  standard_id TEXT NOT NULL REFERENCES document_standard(standard_id) ON DELETE RESTRICT,
  match_score REAL NOT NULL,
  match_dimensions_json TEXT NOT NULL DEFAULT '{}',
  match_reason TEXT NOT NULL,
  decision_status TEXT NOT NULL DEFAULT 'ai_recommended'
    CHECK (decision_status IN ('ai_recommended','user_confirmed','user_rejected','needs_confirmation')),
  created_at TEXT NOT NULL,
  UNIQUE (standard_match_run_id, standard_id)
);

CREATE INDEX IF NOT EXISTS idx_project_document_standard_match_project
  ON project_document_standard_match(project_id, match_score DESC);
