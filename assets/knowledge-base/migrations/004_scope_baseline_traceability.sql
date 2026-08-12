CREATE TABLE IF NOT EXISTS scope_baseline (
  baseline_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  baseline_name TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending_confirmation'
    CHECK (status IN ('draft','pending_confirmation','confirmed','superseded')),
  confirmation_id TEXT REFERENCES confirmation_record(confirmation_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, content_hash),
  UNIQUE (project_id, version_no)
);

CREATE TABLE IF NOT EXISTS scope_baseline_item (
  baseline_id TEXT NOT NULL REFERENCES scope_baseline(baseline_id) ON DELETE CASCADE,
  scope_id TEXT NOT NULL REFERENCES project_scope_item(scope_id) ON DELETE RESTRICT,
  inclusion_status TEXT NOT NULL
    CHECK (inclusion_status IN ('included','excluded','pending')),
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (baseline_id, scope_id)
);

CREATE TABLE IF NOT EXISTS project_traceability_item (
  traceability_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  baseline_id TEXT NOT NULL REFERENCES scope_baseline(baseline_id) ON DELETE CASCADE,
  scope_id TEXT NOT NULL REFERENCES project_scope_item(scope_id) ON DELETE RESTRICT,
  problem_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
  requirement_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
  investment_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
  indicator_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
  benefit_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
  construction_content TEXT NOT NULL,
  investment_category TEXT NOT NULL DEFAULT '',
  target_and_status TEXT NOT NULL DEFAULT '',
  evaluation_method TEXT NOT NULL DEFAULT '',
  expected_benefit TEXT NOT NULL DEFAULT '',
  chapter_location TEXT NOT NULL DEFAULT '',
  completeness INTEGER NOT NULL DEFAULT 0 CHECK (completeness IN (0,1)),
  missing_items_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'incomplete'
    CHECK (status IN ('incomplete','complete','conflict')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (baseline_id, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_scope_baseline_project_status
  ON scope_baseline(project_id, status, version_no);
CREATE INDEX IF NOT EXISTS idx_traceability_project_status
  ON project_traceability_item(project_id, status, scope_id);
