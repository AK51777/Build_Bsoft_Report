PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE shared_knowledge_snapshot_v2 (
  snapshot_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  source_type TEXT NOT NULL
    CHECK (source_type IN ('knowledge_package','policy_catalog','policy_release')),
  source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  server_schema TEXT NOT NULL,
  snapshot_status TEXT NOT NULL CHECK (snapshot_status IN ('current','superseded','stale')),
  fetched_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (project_id, source_type, source_id, content_hash)
);

INSERT INTO shared_knowledge_snapshot_v2 (
  snapshot_id,project_id,source_type,source_id,content_hash,server_schema,
  snapshot_status,fetched_at,metadata_json
)
SELECT
  snapshot_id,project_id,source_type,source_id,content_hash,server_schema,
  snapshot_status,fetched_at,metadata_json
FROM shared_knowledge_snapshot;

CREATE TABLE shared_knowledge_snapshot_item_v2 (
  snapshot_id TEXT NOT NULL REFERENCES shared_knowledge_snapshot_v2(snapshot_id) ON DELETE CASCADE,
  item_type TEXT NOT NULL
    CHECK (item_type IN ('corpus_block','product_capability','policy_catalog_entry','policy_clause')),
  item_id TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (snapshot_id, item_type, item_id)
);

INSERT INTO shared_knowledge_snapshot_item_v2 (
  snapshot_id,item_type,item_id,item_hash,payload_json
)
SELECT snapshot_id,item_type,item_id,item_hash,payload_json
FROM shared_knowledge_snapshot_item;

DROP TABLE shared_knowledge_snapshot_item;
DROP TABLE shared_knowledge_snapshot;
ALTER TABLE shared_knowledge_snapshot_v2 RENAME TO shared_knowledge_snapshot;
ALTER TABLE shared_knowledge_snapshot_item_v2 RENAME TO shared_knowledge_snapshot_item;

CREATE INDEX idx_shared_snapshot_project
  ON shared_knowledge_snapshot(project_id, source_type, snapshot_status, fetched_at);

COMMIT;

PRAGMA foreign_keys = ON;
