CREATE VIRTUAL TABLE IF NOT EXISTS policy_clause_fts USING fts5(
  clause_id UNINDEXED,
  policy_id UNINDEXED,
  original_text,
  normalized_summary,
  topic_tags
);

CREATE VIRTUAL TABLE IF NOT EXISTS corpus_block_fts USING fts5(
  block_id UNINDEXED,
  section_role UNINDEXED,
  module_code UNINDEXED,
  clean_text
);
