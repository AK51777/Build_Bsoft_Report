ALTER TABLE corpus_block ADD COLUMN heading_path_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE product_capability ADD COLUMN category TEXT NOT NULL DEFAULT '';
ALTER TABLE product_capability ADD COLUMN module_name TEXT NOT NULL DEFAULT '';
ALTER TABLE product_capability ADD COLUMN selection_rules_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE product_capability ADD COLUMN source_location TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS section_outline_node (
  outline_node_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES section_composition_plan(plan_id) ON DELETE CASCADE,
  parent_node_id TEXT REFERENCES section_outline_node(outline_node_id) ON DELETE CASCADE,
  chapter_code TEXT NOT NULL,
  heading_level INTEGER NOT NULL CHECK (heading_level BETWEEN 4 AND 7),
  title TEXT NOT NULL,
  node_kind TEXT NOT NULL CHECK (node_kind IN ('scope','capability','feature','subfeature')),
  source_type TEXT NOT NULL CHECK (source_type IN ('scope','capability','corpus')),
  source_object_id TEXT NOT NULL,
  usage_mode TEXT NOT NULL CHECK (usage_mode IN ('direct','parameterized','structure_only','prohibited')),
  ordinal INTEGER NOT NULL CHECK (ordinal > 0),
  length_min INTEGER NOT NULL DEFAULT 0 CHECK (length_min >= 0),
  length_max INTEGER NOT NULL DEFAULT 0 CHECK (length_max >= 0),
  status TEXT NOT NULL CHECK (status IN ('ready','working_only','blocked')),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (plan_id, chapter_code)
);

CREATE INDEX IF NOT EXISTS idx_outline_plan_order
  ON section_outline_node(plan_id, ordinal, chapter_code);
CREATE INDEX IF NOT EXISTS idx_outline_source
  ON section_outline_node(source_type, source_object_id);
