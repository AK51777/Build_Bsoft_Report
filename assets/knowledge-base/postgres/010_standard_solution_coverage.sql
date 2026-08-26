ALTER TABLE medical_report_kb.corpus_block
  ADD COLUMN IF NOT EXISTS source_section_id TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS chunk_index INTEGER NOT NULL DEFAULT 0 CHECK (chunk_index >= 0),
  ADD COLUMN IF NOT EXISTS source_is_heading BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_corpus_block_source_section
  ON medical_report_kb.corpus_block(corpus_document_id, source_order, source_section_id, chunk_index);

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
  document.source_corpus_type,
  document.metadata
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
  block.source_section_id,
  block.chunk_index,
  block.source_is_heading,
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
  block.construction_scope_tags,
  block.content_format,
  block.content_payload,
  block.asset_manifest,
  block.visible_text_hash
FROM medical_report_kb.knowledge_package AS package
JOIN medical_report_kb.corpus_document AS document ON document.package_id = package.package_id
JOIN medical_report_kb.corpus_block AS block ON block.corpus_document_id = document.corpus_document_id
WHERE package.package_status = 'published'
  AND document.review_status = 'approved'
  AND block.review_status = 'approved';
