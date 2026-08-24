ALTER TABLE corpus_document ADD COLUMN source_corpus_type TEXT NOT NULL DEFAULT 'legacy_unspecified'
  CHECK (source_corpus_type IN ('standard_solution','reference_feasibility','generic_reference','legacy_unspecified'));

ALTER TABLE corpus_block ADD COLUMN content_type TEXT NOT NULL DEFAULT 'legacy_unspecified'
  CHECK (content_type IN ('construction_solution','feasibility_narrative','common_narrative','project_specific','structure_only','legacy_unspecified'));
ALTER TABLE corpus_block ADD COLUMN semantic_section TEXT NOT NULL DEFAULT '';
ALTER TABLE corpus_block ADD COLUMN content_slot TEXT NOT NULL DEFAULT '';
ALTER TABLE corpus_block ADD COLUMN source_order INTEGER NOT NULL DEFAULT 0 CHECK (source_order >= 0);
ALTER TABLE corpus_block ADD COLUMN adaptation_mode TEXT NOT NULL DEFAULT 'structure_only'
  CHECK (adaptation_mode IN ('direct','parameterized','structure_only','prohibited'));
ALTER TABLE corpus_block ADD COLUMN assessment_targets_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE corpus_block ADD COLUMN construction_scope_tags_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_corpus_document_source_type
  ON corpus_document(source_corpus_type, document_type, project_type, review_status);
CREATE INDEX IF NOT EXISTS idx_corpus_block_semantic_route
  ON corpus_block(section_role, semantic_section, content_slot, content_type, review_status, source_order);
