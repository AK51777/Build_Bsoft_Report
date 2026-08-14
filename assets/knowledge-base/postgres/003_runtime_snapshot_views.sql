ALTER TABLE medical_report_kb.corpus_block
  DROP CONSTRAINT IF EXISTS corpus_block_corpus_document_id_text_hash_key;

CREATE OR REPLACE VIEW medical_report_kb.runtime_knowledge_package AS
SELECT
  package_id,
  schema_version,
  title,
  permission_scope,
  content_hash,
  review_summary,
  published_at
FROM medical_report_kb.knowledge_package
WHERE package_status = 'published';

CREATE OR REPLACE VIEW medical_report_kb.runtime_package_source AS
SELECT
  relation.package_id,
  relation.source_role,
  source.source_id,
  source.file_name,
  source.file_type,
  source.source_sha256,
  source.permission_scope,
  source.verification_status
FROM medical_report_kb.package_source AS relation
JOIN medical_report_kb.runtime_knowledge_package AS package ON package.package_id = relation.package_id
JOIN medical_report_kb.source_document AS source ON source.source_id = relation.source_id
WHERE source.verification_status = 'verified';

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
  document.version
FROM medical_report_kb.corpus_document AS document
JOIN medical_report_kb.runtime_knowledge_package AS package ON package.package_id = document.package_id
WHERE document.review_status = 'approved';
