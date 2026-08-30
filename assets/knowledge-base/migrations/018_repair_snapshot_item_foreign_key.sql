PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE shared_knowledge_snapshot_item_v3 (
  snapshot_id TEXT NOT NULL REFERENCES shared_knowledge_snapshot(snapshot_id) ON DELETE CASCADE,
  item_type TEXT NOT NULL
    CHECK (item_type IN ('corpus_block','product_capability','policy_catalog_entry','policy_clause')),
  item_id TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (snapshot_id, item_type, item_id)
);

INSERT INTO shared_knowledge_snapshot_item_v3 (
  snapshot_id,item_type,item_id,item_hash,payload_json
)
SELECT snapshot_id,item_type,item_id,item_hash,payload_json
FROM shared_knowledge_snapshot_item;

DROP TABLE shared_knowledge_snapshot_item;
ALTER TABLE shared_knowledge_snapshot_item_v3 RENAME TO shared_knowledge_snapshot_item;

COMMIT;

PRAGMA foreign_keys = ON;
