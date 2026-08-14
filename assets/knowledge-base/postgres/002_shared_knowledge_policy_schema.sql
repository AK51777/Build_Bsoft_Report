CREATE SCHEMA IF NOT EXISTS medical_report_kb;

CREATE TABLE IF NOT EXISTS medical_report_kb.schema_migration (
  version TEXT PRIMARY KEY,
  file_hash TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS medical_report_kb.knowledge_package (
  package_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  title TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  package_status TEXT NOT NULL CHECK (package_status IN ('reviewed','published','retired')),
  content_hash TEXT NOT NULL,
  review_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  retired_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS medical_report_kb.source_document (
  source_id TEXT PRIMARY KEY,
  source_scope TEXT NOT NULL CHECK (source_scope IN ('company_shared','public_policy','internal_reference')),
  file_name TEXT NOT NULL,
  file_type TEXT NOT NULL,
  source_uri TEXT NOT NULL DEFAULT '',
  source_sha256 TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  verification_status TEXT NOT NULL CHECK (verification_status IN ('verified','partially_verified','unverified')),
  contains_personal_data BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (source_scope, source_sha256)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.package_source (
  package_id TEXT NOT NULL REFERENCES medical_report_kb.knowledge_package(package_id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES medical_report_kb.source_document(source_id) ON DELETE RESTRICT,
  source_role TEXT NOT NULL,
  PRIMARY KEY (package_id, source_id, source_role)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.corpus_document (
  corpus_document_id TEXT PRIMARY KEY,
  package_id TEXT NOT NULL REFERENCES medical_report_kb.knowledge_package(package_id) ON DELETE RESTRICT,
  source_id TEXT NOT NULL REFERENCES medical_report_kb.source_document(source_id) ON DELETE RESTRICT,
  document_type TEXT NOT NULL,
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  project_type TEXT NOT NULL,
  quality_level TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN ('approved','prohibited','retired')),
  version TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS medical_report_kb.corpus_block (
  block_id TEXT PRIMARY KEY,
  corpus_document_id TEXT NOT NULL REFERENCES medical_report_kb.corpus_document(corpus_document_id) ON DELETE CASCADE,
  block_index INTEGER NOT NULL CHECK (block_index > 0),
  source_location TEXT NOT NULL,
  heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
  section_role TEXT NOT NULL,
  module_code TEXT NOT NULL DEFAULT '',
  clean_text TEXT NOT NULL,
  reuse_class TEXT NOT NULL CHECK (reuse_class IN ('A','B','C','D')),
  quality_level TEXT NOT NULL,
  applicable_document_types JSONB NOT NULL DEFAULT '[]'::jsonb,
  applicable_project_types JSONB NOT NULL DEFAULT '[]'::jsonb,
  prerequisites JSONB NOT NULL DEFAULT '[]'::jsonb,
  variable_slots JSONB NOT NULL DEFAULT '[]'::jsonb,
  forbidden_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
  length_band TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL CHECK (review_status IN ('approved','prohibited','retired')),
  text_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (corpus_document_id, block_index),
  UNIQUE (corpus_document_id, text_hash)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.product_capability (
  capability_id TEXT PRIMARY KEY,
  package_id TEXT NOT NULL REFERENCES medical_report_kb.knowledge_package(package_id) ON DELETE RESTRICT,
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  capability_name TEXT NOT NULL,
  capability_description TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  module_name TEXT NOT NULL DEFAULT '',
  selection_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
  prerequisites JSONB NOT NULL DEFAULT '[]'::jsonb,
  interface_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
  exclusions JSONB NOT NULL DEFAULT '[]'::jsonb,
  applicable_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_location TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL CHECK (review_status IN ('approved','prohibited','retired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (package_id, product_code, capability_name)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.capability_block (
  capability_id TEXT NOT NULL REFERENCES medical_report_kb.product_capability(capability_id) ON DELETE CASCADE,
  block_id TEXT NOT NULL REFERENCES medical_report_kb.corpus_block(block_id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL DEFAULT 'standard_description'
    CHECK (relation_type IN ('standard_description','prerequisite','implementation','benefit','operation')),
  priority INTEGER NOT NULL DEFAULT 100 CHECK (priority > 0),
  review_status TEXT NOT NULL DEFAULT 'approved' CHECK (review_status IN ('approved','prohibited','retired')),
  PRIMARY KEY (capability_id, block_id, relation_type)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.tag (
  tag_id TEXT PRIMARY KEY,
  tag_code TEXT NOT NULL UNIQUE,
  tag_name TEXT NOT NULL,
  tag_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired'))
);

CREATE TABLE IF NOT EXISTS medical_report_kb.corpus_block_tag (
  block_id TEXT NOT NULL REFERENCES medical_report_kb.corpus_block(block_id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES medical_report_kb.tag(tag_id) ON DELETE RESTRICT,
  PRIMARY KEY (block_id, tag_id)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_catalog (
  catalog_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  catalog_scope TEXT NOT NULL,
  title TEXT NOT NULL,
  source_file_name TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  worksheet_name TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  catalog_status TEXT NOT NULL CHECK (catalog_status IN ('reviewed','published','superseded','retired')),
  record_count INTEGER NOT NULL CHECK (record_count >= 0),
  content_hash TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at TIMESTAMPTZ,
  superseded_at TIMESTAMPTZ,
  UNIQUE (catalog_scope, source_sha256, worksheet_name)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_policy_catalog_one_published_scope
  ON medical_report_kb.policy_catalog(catalog_scope)
  WHERE catalog_status = 'published';

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_catalog_entry (
  catalog_entry_id TEXT PRIMARY KEY,
  catalog_id TEXT NOT NULL REFERENCES medical_report_kb.policy_catalog(catalog_id) ON DELETE CASCADE,
  source_row INTEGER NOT NULL CHECK (source_row > 0),
  source_index_no TEXT NOT NULL,
  identity_key TEXT NOT NULL,
  catalog_group_code TEXT NOT NULL DEFAULT '',
  catalog_group_name TEXT NOT NULL DEFAULT '',
  authority_level_label TEXT NOT NULL DEFAULT '',
  category_name TEXT NOT NULL DEFAULT '',
  keyword_text TEXT NOT NULL DEFAULT '',
  keyword_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  document_no TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  publish_date DATE,
  publish_date_raw TEXT NOT NULL DEFAULT '',
  issuer TEXT NOT NULL DEFAULT '',
  file_count INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  external_url TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  entry_status TEXT NOT NULL DEFAULT 'active' CHECK (entry_status IN ('active','retired','invalid')),
  row_hash TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (catalog_id, source_index_no),
  UNIQUE (catalog_id, source_row)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_document (
  policy_id TEXT PRIMARY KEY,
  catalog_entry_id TEXT REFERENCES medical_report_kb.policy_catalog_entry(catalog_entry_id) ON DELETE SET NULL,
  official_source_id TEXT REFERENCES medical_report_kb.source_document(source_id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  document_no TEXT NOT NULL DEFAULT '',
  issuer TEXT NOT NULL,
  authority_group INTEGER NOT NULL,
  authority_rank INTEGER NOT NULL DEFAULT 99,
  jurisdiction_level TEXT NOT NULL CHECK (jurisdiction_level IN ('national','province','prefecture','county','other')),
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  jurisdiction_name TEXT NOT NULL DEFAULT '',
  policy_type TEXT NOT NULL,
  publish_date DATE,
  effective_date DATE,
  expiry_date DATE,
  validity_status TEXT NOT NULL DEFAULT 'needs_verification'
    CHECK (validity_status IN ('current','expired','repealed','replaced','historical','needs_verification')),
  official_url TEXT NOT NULL,
  official_domain TEXT NOT NULL,
  source_hash TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  publication_status TEXT NOT NULL DEFAULT 'draft'
    CHECK (publication_status IN ('draft','published','retired')),
  permission_scope TEXT NOT NULL DEFAULT 'internal_company_reference',
  retrieved_at TIMESTAMPTZ,
  last_verified_at TIMESTAMPTZ,
  notes TEXT NOT NULL DEFAULT '',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (normalized_title, document_no, issuer)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_clause (
  clause_id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES medical_report_kb.policy_document(policy_id) ON DELETE CASCADE,
  clause_order INTEGER NOT NULL CHECK (clause_order > 0),
  article_path TEXT NOT NULL,
  source_location TEXT NOT NULL DEFAULT '',
  original_text TEXT NOT NULL,
  normalized_summary TEXT NOT NULL,
  target_objects JSONB NOT NULL DEFAULT '[]'::jsonb,
  requirement_type TEXT NOT NULL
    CHECK (requirement_type IN ('mandatory','guiding','target','encouraging','evaluation','background')),
  applicability_notes TEXT NOT NULL DEFAULT '',
  permitted_sections JSONB NOT NULL DEFAULT '[]'::jsonb,
  forbidden_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('approved','pending','prohibited','retired')),
  text_hash TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (policy_id, article_path, text_hash)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_topic (
  topic_id TEXT PRIMARY KEY,
  topic_code TEXT NOT NULL UNIQUE,
  topic_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired'))
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_clause_topic (
  clause_id TEXT NOT NULL REFERENCES medical_report_kb.policy_clause(clause_id) ON DELETE CASCADE,
  topic_id TEXT NOT NULL REFERENCES medical_report_kb.policy_topic(topic_id) ON DELETE RESTRICT,
  PRIMARY KEY (clause_id, topic_id)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_relation (
  relation_id TEXT PRIMARY KEY,
  from_policy_id TEXT NOT NULL REFERENCES medical_report_kb.policy_document(policy_id) ON DELETE CASCADE,
  to_policy_id TEXT NOT NULL REFERENCES medical_report_kb.policy_document(policy_id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL CHECK (relation_type IN ('replaces','repeals','amends','implements','based_on','supports')),
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  evidence_note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (from_policy_id, to_policy_id, relation_type)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.policy_verification_event (
  verification_id TEXT PRIMARY KEY,
  policy_id TEXT REFERENCES medical_report_kb.policy_document(policy_id) ON DELETE CASCADE,
  catalog_entry_id TEXT REFERENCES medical_report_kb.policy_catalog_entry(catalog_entry_id) ON DELETE CASCADE,
  checked_url TEXT NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL,
  check_method TEXT NOT NULL,
  result TEXT NOT NULL,
  previous_status TEXT NOT NULL DEFAULT '',
  current_status TEXT NOT NULL,
  evidence_hash TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  CHECK (policy_id IS NOT NULL OR catalog_entry_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.import_run (
  import_run_id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL CHECK (target_type IN ('knowledge_package','policy_catalog','policy_document')),
  target_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  importer_version TEXT NOT NULL,
  run_status TEXT NOT NULL CHECK (run_status IN ('running','completed','failed')),
  row_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_details JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  UNIQUE (target_type, target_id, input_hash, importer_version)
);

CREATE TABLE IF NOT EXISTS medical_report_kb.review_event (
  review_event_id TEXT PRIMARY KEY,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  reviewed_by TEXT NOT NULL,
  reviewed_at TIMESTAMPTZ NOT NULL,
  before_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_value JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_corpus_document_runtime
  ON medical_report_kb.corpus_document(package_id, project_type, document_type, review_status);

CREATE INDEX IF NOT EXISTS idx_corpus_block_runtime
  ON medical_report_kb.corpus_block(section_role, module_code, reuse_class, review_status);

CREATE INDEX IF NOT EXISTS idx_capability_runtime
  ON medical_report_kb.product_capability(product_code, review_status, capability_name);

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_lookup
  ON medical_report_kb.policy_catalog_entry(catalog_id, category_name, authority_level_label, entry_status);

CREATE INDEX IF NOT EXISTS idx_policy_catalog_entry_identity
  ON medical_report_kb.policy_catalog_entry(identity_key);

CREATE INDEX IF NOT EXISTS idx_policy_document_runtime
  ON medical_report_kb.policy_document(jurisdiction_level, jurisdiction_code, validity_status, verification_status, publication_status);

CREATE INDEX IF NOT EXISTS idx_policy_clause_runtime
  ON medical_report_kb.policy_clause(policy_id, requirement_type, verification_status, review_status);

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
  block.text_hash
FROM medical_report_kb.knowledge_package AS package
JOIN medical_report_kb.corpus_document AS document ON document.package_id = package.package_id
JOIN medical_report_kb.corpus_block AS block ON block.corpus_document_id = document.corpus_document_id
WHERE package.package_status = 'published'
  AND document.review_status = 'approved'
  AND block.review_status = 'approved';

CREATE OR REPLACE VIEW medical_report_kb.runtime_product_capability AS
SELECT
  capability.*,
  package.content_hash AS package_content_hash
FROM medical_report_kb.product_capability AS capability
JOIN medical_report_kb.knowledge_package AS package ON package.package_id = capability.package_id
WHERE package.package_status = 'published'
  AND capability.review_status = 'approved';

CREATE OR REPLACE VIEW medical_report_kb.runtime_capability_block AS
SELECT relation.*
FROM medical_report_kb.capability_block AS relation
JOIN medical_report_kb.runtime_product_capability AS capability ON capability.capability_id = relation.capability_id
JOIN medical_report_kb.runtime_corpus_block AS block ON block.block_id = relation.block_id
WHERE relation.review_status = 'approved';

CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_catalog_entry AS
SELECT entry.*
FROM medical_report_kb.policy_catalog_entry AS entry
JOIN medical_report_kb.policy_catalog AS catalog ON catalog.catalog_id = entry.catalog_id
WHERE catalog.catalog_status = 'published'
  AND entry.entry_status = 'active';

CREATE OR REPLACE VIEW medical_report_kb.runtime_policy_clause AS
SELECT
  policy.policy_id,
  policy.title,
  policy.document_no,
  policy.issuer,
  policy.authority_group,
  policy.authority_rank,
  policy.jurisdiction_level,
  policy.jurisdiction_code,
  policy.jurisdiction_name,
  policy.policy_type,
  policy.publish_date,
  policy.effective_date,
  policy.expiry_date,
  policy.validity_status,
  policy.official_url,
  policy.official_domain,
  policy.source_hash,
  policy.last_verified_at,
  clause.clause_id,
  clause.clause_order,
  clause.article_path,
  clause.source_location,
  clause.original_text,
  clause.normalized_summary,
  clause.target_objects,
  clause.requirement_type,
  clause.applicability_notes,
  clause.permitted_sections,
  clause.forbidden_claims,
  clause.text_hash,
  COALESCE(
    jsonb_agg(
      jsonb_build_object('topic_code', topic.topic_code, 'topic_name', topic.topic_name)
      ORDER BY topic.topic_code
    ) FILTER (WHERE topic.topic_id IS NOT NULL),
    '[]'::jsonb
  ) AS topics
FROM medical_report_kb.policy_document AS policy
JOIN medical_report_kb.policy_clause AS clause ON clause.policy_id = policy.policy_id
LEFT JOIN medical_report_kb.policy_clause_topic AS relation ON relation.clause_id = clause.clause_id
LEFT JOIN medical_report_kb.policy_topic AS topic ON topic.topic_id = relation.topic_id AND topic.status = 'active'
WHERE policy.publication_status = 'published'
  AND policy.validity_status = 'current'
  AND policy.verification_status = 'verified'
  AND clause.verification_status = 'verified'
  AND clause.review_status = 'approved'
GROUP BY policy.policy_id, clause.clause_id;
