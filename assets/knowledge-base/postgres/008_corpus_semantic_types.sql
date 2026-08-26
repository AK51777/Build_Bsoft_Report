ALTER TABLE medical_report_kb.corpus_document
  ADD COLUMN IF NOT EXISTS source_corpus_type TEXT NOT NULL DEFAULT 'legacy_unspecified';
ALTER TABLE medical_report_kb.corpus_document
  DROP CONSTRAINT IF EXISTS corpus_document_source_corpus_type_check;
ALTER TABLE medical_report_kb.corpus_document
  ADD CONSTRAINT corpus_document_source_corpus_type_check
  CHECK (source_corpus_type IN ('standard_solution','reference_feasibility','generic_reference','legacy_unspecified'));

ALTER TABLE medical_report_kb.corpus_block
  ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'legacy_unspecified',
  ADD COLUMN IF NOT EXISTS semantic_section TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS content_slot TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS source_order INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS adaptation_mode TEXT NOT NULL DEFAULT 'structure_only',
  ADD COLUMN IF NOT EXISTS assessment_targets JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS construction_scope_tags JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE medical_report_kb.corpus_block
  DROP CONSTRAINT IF EXISTS corpus_block_content_type_check;
ALTER TABLE medical_report_kb.corpus_block
  ADD CONSTRAINT corpus_block_content_type_check
  CHECK (content_type IN ('construction_solution','feasibility_narrative','common_narrative','project_specific','structure_only','legacy_unspecified'));
ALTER TABLE medical_report_kb.corpus_block
  DROP CONSTRAINT IF EXISTS corpus_block_adaptation_mode_check;
ALTER TABLE medical_report_kb.corpus_block
  ADD CONSTRAINT corpus_block_adaptation_mode_check
  CHECK (adaptation_mode IN ('direct','parameterized','structure_only','prohibited'));
ALTER TABLE medical_report_kb.corpus_block
  DROP CONSTRAINT IF EXISTS corpus_block_source_order_check;
ALTER TABLE medical_report_kb.corpus_block
  ADD CONSTRAINT corpus_block_source_order_check CHECK (source_order >= 0);

CREATE INDEX IF NOT EXISTS idx_corpus_document_source_type
  ON medical_report_kb.corpus_document(source_corpus_type, document_type, project_type, review_status);
CREATE INDEX IF NOT EXISTS idx_corpus_block_semantic_route
  ON medical_report_kb.corpus_block(section_role, semantic_section, content_slot, content_type, review_status, source_order);

CREATE OR REPLACE VIEW medical_report_kb.runtime_corpus_document AS
SELECT
  document.corpus_document_id,
  document.package_id,
  document.source_id,
  document.document_type,
  document.jurisdiction_code,
  document.project_type,
  document.quality_level,
  document.permission_scope,
  document.version,
  document.source_corpus_type
FROM medical_report_kb.corpus_document AS document
JOIN medical_report_kb.runtime_knowledge_package AS package ON package.package_id = document.package_id
WHERE document.review_status = 'approved';

CREATE OR REPLACE VIEW medical_report_kb.runtime_corpus_block AS
SELECT
  package.package_id,
  package.schema_version AS package_schema_version,
  package.content_hash AS package_content_hash,
  document.corpus_document_id,
  document.document_type,
  document.project_type,
  document.permission_scope,
  block.block_id,
  block.block_index,
  block.source_location,
  block.heading_path,
  block.section_role,
  block.module_code,
  block.clean_text,
  block.reuse_class,
  block.quality_level,
  block.applicable_document_types,
  block.applicable_project_types,
  block.prerequisites,
  block.variable_slots,
  block.forbidden_terms,
  block.length_band,
  block.text_hash,
  document.source_corpus_type,
  block.content_type,
  block.semantic_section,
  block.content_slot,
  block.source_order,
  block.adaptation_mode,
  block.assessment_targets,
  block.construction_scope_tags
FROM medical_report_kb.knowledge_package AS package
JOIN medical_report_kb.corpus_document AS document ON document.package_id = package.package_id
JOIN medical_report_kb.corpus_block AS block ON block.corpus_document_id = document.corpus_document_id
WHERE package.package_status = 'published'
  AND document.review_status = 'approved'
  AND block.review_status = 'approved';
