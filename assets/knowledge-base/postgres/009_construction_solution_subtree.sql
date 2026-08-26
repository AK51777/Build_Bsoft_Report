-- Shared PostgreSQL stores standard knowledge only. Project scope, matching
-- decisions and assembly manifests remain in the project-local SQLite file.

ALTER TABLE medical_report_kb.corpus_block
  ADD COLUMN IF NOT EXISTS content_format TEXT NOT NULL DEFAULT 'plain_text',
  ADD COLUMN IF NOT EXISTS content_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS asset_manifest JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS visible_text_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE medical_report_kb.corpus_block
  DROP CONSTRAINT IF EXISTS corpus_block_content_format_check;
ALTER TABLE medical_report_kb.corpus_block
  ADD CONSTRAINT corpus_block_content_format_check
  CHECK (content_format IN ('plain_text','structured_json','ooxml_fragment'));

UPDATE medical_report_kb.corpus_block
SET visible_text_hash = text_hash
WHERE visible_text_hash = '';

ALTER TABLE medical_report_kb.capability_block
  ADD COLUMN IF NOT EXISTS root_heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS relation_order INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS verbatim_eligible BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE medical_report_kb.capability_block
  DROP CONSTRAINT IF EXISTS capability_block_relation_order_check;
ALTER TABLE medical_report_kb.capability_block
  ADD CONSTRAINT capability_block_relation_order_check CHECK (relation_order >= 0);

CREATE INDEX IF NOT EXISTS idx_capability_block_subtree
  ON medical_report_kb.capability_block(capability_id, relation_order, priority, block_id);

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

CREATE OR REPLACE VIEW medical_report_kb.runtime_capability_block AS
SELECT
  relation.capability_id,
  relation.block_id,
  relation.relation_type,
  relation.priority,
  relation.review_status,
  relation.root_heading_path,
  relation.relation_order,
  relation.verbatim_eligible
FROM medical_report_kb.capability_block AS relation
JOIN medical_report_kb.product_capability AS capability
  ON capability.capability_id = relation.capability_id
JOIN medical_report_kb.knowledge_package AS package
  ON package.package_id = capability.package_id
JOIN medical_report_kb.corpus_block AS block
  ON block.block_id = relation.block_id
WHERE package.package_status = 'published'
  AND capability.review_status = 'approved'
  AND relation.review_status = 'approved'
  AND block.review_status = 'approved';
