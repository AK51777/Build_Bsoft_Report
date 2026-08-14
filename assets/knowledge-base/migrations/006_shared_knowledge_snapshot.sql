CREATE TABLE IF NOT EXISTS shared_knowledge_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  source_type TEXT NOT NULL CHECK (source_type IN ('knowledge_package','policy_release')),
  source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  server_schema TEXT NOT NULL,
  snapshot_status TEXT NOT NULL CHECK (snapshot_status IN ('current','superseded','stale')),
  fetched_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (project_id, source_type, source_id, content_hash)
);

CREATE TABLE IF NOT EXISTS shared_knowledge_snapshot_item (
  snapshot_id TEXT NOT NULL REFERENCES shared_knowledge_snapshot(snapshot_id) ON DELETE CASCADE,
  item_type TEXT NOT NULL CHECK (item_type IN ('corpus_block','product_capability','policy_clause')),
  item_id TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (snapshot_id, item_type, item_id)
);

CREATE INDEX IF NOT EXISTS idx_shared_snapshot_project
  ON shared_knowledge_snapshot(project_id, source_type, snapshot_status, fetched_at);
