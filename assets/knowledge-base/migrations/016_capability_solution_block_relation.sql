CREATE TABLE IF NOT EXISTS capability_solution_block_relation (
  source_package_id TEXT NOT NULL,
  package_content_hash TEXT NOT NULL DEFAULT '',
  capability_id TEXT NOT NULL
    REFERENCES product_capability(capability_id) ON DELETE CASCADE,
  block_id TEXT NOT NULL
    REFERENCES corpus_block(block_id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL DEFAULT 'standard_description',
  priority INTEGER NOT NULL DEFAULT 100 CHECK (priority >= 0),
  review_status TEXT NOT NULL DEFAULT 'approved',
  root_heading_path_json TEXT NOT NULL DEFAULT '[]',
  relation_order INTEGER NOT NULL DEFAULT 0 CHECK (relation_order >= 0),
  verbatim_eligible INTEGER NOT NULL DEFAULT 1 CHECK (verbatim_eligible IN (0,1)),
  PRIMARY KEY (source_package_id, capability_id, block_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_solution_root
  ON capability_solution_block_relation(
    source_package_id, capability_id, relation_order, priority, block_id
  );
