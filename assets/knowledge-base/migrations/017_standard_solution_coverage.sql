ALTER TABLE corpus_block ADD COLUMN source_section_id TEXT NOT NULL DEFAULT '';
ALTER TABLE corpus_block ADD COLUMN chunk_index INTEGER NOT NULL DEFAULT 0 CHECK (chunk_index >= 0);
ALTER TABLE corpus_block ADD COLUMN source_is_heading INTEGER NOT NULL DEFAULT 0 CHECK (source_is_heading IN (0,1));

CREATE INDEX IF NOT EXISTS idx_corpus_block_source_section
  ON corpus_block(corpus_document_id, source_order, source_section_id, chunk_index);
