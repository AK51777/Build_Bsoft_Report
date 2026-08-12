PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS kb_schema_migration (
  version TEXT PRIMARY KEY,
  file_hash TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
  project_id TEXT PRIMARY KEY,
  project_code TEXT NOT NULL UNIQUE,
  official_name TEXT NOT NULL,
  document_type TEXT NOT NULL,
  owner_name TEXT NOT NULL DEFAULT '',
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  jurisdiction_name TEXT NOT NULL DEFAULT '',
  project_type TEXT NOT NULL DEFAULT 'hospital_informationization',
  scope_authority TEXT NOT NULL DEFAULT '',
  acceptance_targets_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','frozen','archived')),
  baseline_version TEXT NOT NULL DEFAULT 'working',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_document (
  source_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES project(project_id) ON DELETE CASCADE,
  source_scope TEXT NOT NULL CHECK (source_scope IN ('project','shared')),
  source_class TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_type TEXT NOT NULL,
  source_path TEXT NOT NULL DEFAULT '',
  official_url TEXT NOT NULL DEFAULT '',
  issuer TEXT NOT NULL DEFAULT '',
  document_date TEXT NOT NULL DEFAULT '',
  statistical_date TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL DEFAULT '',
  usage_scope TEXT NOT NULL DEFAULT '',
  restriction_note TEXT NOT NULL DEFAULT '',
  contains_personal_data INTEGER NOT NULL DEFAULT 0 CHECK (contains_personal_data IN (0,1)),
  verification_status TEXT NOT NULL DEFAULT 'registered'
    CHECK (verification_status IN ('registered','verified','partially_verified','unverified','retired')),
  imported_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS evidence_record (
  evidence_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source_document(source_id) ON DELETE CASCADE,
  source_location TEXT NOT NULL,
  evidence_text TEXT NOT NULL,
  evidence_hash TEXT NOT NULL,
  extraction_method TEXT NOT NULL DEFAULT 'manual',
  reliability_level TEXT NOT NULL DEFAULT 'B' CHECK (reliability_level IN ('A','B','C','D')),
  verified_at TEXT,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS project_fact (
  fact_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  fact_key TEXT NOT NULL,
  fact_category TEXT NOT NULL,
  fact_content TEXT NOT NULL,
  normalized_value TEXT NOT NULL DEFAULT '',
  data_unit TEXT NOT NULL DEFAULT '',
  statistical_date TEXT NOT NULL DEFAULT '',
  fact_status TEXT NOT NULL CHECK (
    fact_status IN (
      'confirmed','material_explicit','pending_confirmation','pending_supplement',
      'conflict','analysis_recommendation','reference_only','not_applicable'
    )
  ),
  materiality TEXT NOT NULL DEFAULT 'B' CHECK (materiality IN ('A','B','C','D')),
  confirmation_required INTEGER NOT NULL DEFAULT 1 CHECK (confirmation_required IN (0,1)),
  conflict_group_id TEXT NOT NULL DEFAULT '',
  allowed_chapters_json TEXT NOT NULL DEFAULT '[]',
  sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','internal','personal','sensitive')),
  frozen_version TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, fact_key, fact_content)
);

CREATE TABLE IF NOT EXISTS fact_evidence (
  fact_id TEXT NOT NULL REFERENCES project_fact(fact_id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence_record(evidence_id) ON DELETE CASCADE,
  evidence_role TEXT NOT NULL DEFAULT 'support' CHECK (evidence_role IN ('support','contradict','context')),
  PRIMARY KEY (fact_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS inference_record (
  inference_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  proposition TEXT NOT NULL,
  inference_type TEXT NOT NULL CHECK (
    inference_type IN ('calculation','policy_derivation','technical_default','context_inference','project_assumption')
  ),
  premise_ids_json TEXT NOT NULL DEFAULT '[]',
  reasoning_note TEXT NOT NULL,
  materiality TEXT NOT NULL DEFAULT 'B' CHECK (materiality IN ('A','B','C','D')),
  confirmation_required INTEGER NOT NULL DEFAULT 1 CHECK (confirmation_required IN (0,1)),
  allowed_expression TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending_confirmation'
    CHECK (status IN ('pending_confirmation','confirmed','rejected','analysis_only','not_applicable')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmation_question (
  question_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL CHECK (target_type IN ('fact','inference','conflict','missing','policy','format','scope')),
  target_id TEXT NOT NULL,
  question_text TEXT NOT NULL,
  materiality TEXT NOT NULL DEFAULT 'B' CHECK (materiality IN ('A','B','C','D')),
  impact_type TEXT NOT NULL CHECK (impact_type IN ('boundary','investment','technical','text','compliance','delivery')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','deferred','cancelled')),
  batch_no INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmation_record (
  confirmation_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('confirm','reject','modify','defer','exclude')),
  before_value_json TEXT NOT NULL DEFAULT '{}',
  after_value_json TEXT NOT NULL DEFAULT '{}',
  decision_note TEXT NOT NULL DEFAULT '',
  confirmed_by TEXT NOT NULL DEFAULT '',
  confirmed_at TEXT NOT NULL,
  baseline_version TEXT NOT NULL DEFAULT 'working'
);

CREATE TABLE IF NOT EXISTS stage_gate_result (
  gate_result_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  stage_code TEXT NOT NULL,
  gate_code TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('pass','warning','fail','skipped')),
  issue_count INTEGER NOT NULL DEFAULT 0,
  blocking_count INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL,
  UNIQUE (project_id, stage_code, gate_code, checked_at)
);

CREATE TABLE IF NOT EXISTS policy_document (
  policy_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  document_no TEXT NOT NULL DEFAULT '',
  issuer TEXT NOT NULL,
  authority_group INTEGER NOT NULL,
  authority_rank INTEGER NOT NULL DEFAULT 99,
  jurisdiction_level TEXT NOT NULL CHECK (jurisdiction_level IN ('national','province','prefecture','county','other')),
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  jurisdiction_name TEXT NOT NULL DEFAULT '',
  policy_type TEXT NOT NULL,
  publish_date TEXT NOT NULL DEFAULT '',
  effective_date TEXT NOT NULL DEFAULT '',
  expiry_date TEXT NOT NULL DEFAULT '',
  validity_status TEXT NOT NULL DEFAULT 'needs_verification'
    CHECK (validity_status IN ('current','expired','repealed','replaced','historical','needs_verification')),
  official_url TEXT NOT NULL,
  official_domain TEXT NOT NULL,
  source_hash TEXT NOT NULL DEFAULT '',
  retrieved_at TEXT NOT NULL,
  last_verified_at TEXT NOT NULL DEFAULT '',
  verification_status TEXT NOT NULL DEFAULT 'unverified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  source_document_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE (title, document_no, issuer)
);

CREATE TABLE IF NOT EXISTS policy_clause (
  clause_id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES policy_document(policy_id) ON DELETE CASCADE,
  article_path TEXT NOT NULL,
  original_text TEXT NOT NULL,
  normalized_summary TEXT NOT NULL,
  topic_tags_json TEXT NOT NULL DEFAULT '[]',
  target_objects_json TEXT NOT NULL DEFAULT '[]',
  requirement_type TEXT NOT NULL CHECK (requirement_type IN ('mandatory','guiding','target','encouraging','evaluation','background')),
  applicability_notes TEXT NOT NULL DEFAULT '',
  text_hash TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'verified'
    CHECK (verification_status IN ('verified','partially_verified','unverified')),
  UNIQUE (policy_id, article_path, text_hash)
);

CREATE TABLE IF NOT EXISTS policy_relation (
  relation_id TEXT PRIMARY KEY,
  from_policy_id TEXT NOT NULL REFERENCES policy_document(policy_id) ON DELETE CASCADE,
  to_policy_id TEXT NOT NULL REFERENCES policy_document(policy_id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL CHECK (relation_type IN ('replaces','repeals','amends','implements','based_on','supports')),
  verified_status TEXT NOT NULL DEFAULT 'unverified',
  evidence_note TEXT NOT NULL DEFAULT '',
  UNIQUE (from_policy_id, to_policy_id, relation_type)
);

CREATE TABLE IF NOT EXISTS policy_match_run (
  match_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  topic_tags_json TEXT NOT NULL DEFAULT '[]',
  matcher_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_policy_match (
  match_id TEXT PRIMARY KEY,
  match_run_id TEXT NOT NULL REFERENCES policy_match_run(match_run_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  policy_id TEXT NOT NULL REFERENCES policy_document(policy_id) ON DELETE CASCADE,
  clause_id TEXT REFERENCES policy_clause(clause_id) ON DELETE CASCADE,
  match_dimensions_json TEXT NOT NULL DEFAULT '{}',
  relevance_level TEXT NOT NULL CHECK (relevance_level IN ('core','important','supplementary','not_applicable')),
  basis_use INTEGER NOT NULL DEFAULT 0 CHECK (basis_use IN (0,1)),
  background_use INTEGER NOT NULL DEFAULT 0 CHECK (background_use IN (0,1)),
  other_chapter_use_json TEXT NOT NULL DEFAULT '[]',
  project_relation TEXT NOT NULL,
  sort_key TEXT NOT NULL,
  basis_order INTEGER,
  decision_status TEXT NOT NULL DEFAULT 'ai_recommended'
    CHECK (decision_status IN ('ai_recommended','user_confirmed','user_excluded','needs_confirmation')),
  decision_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (match_run_id, policy_id, clause_id)
);

CREATE TABLE IF NOT EXISTS policy_citation (
  citation_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  match_id TEXT NOT NULL REFERENCES project_policy_match(match_id) ON DELETE CASCADE,
  report_section TEXT NOT NULL,
  citation_purpose TEXT NOT NULL CHECK (citation_purpose IN ('basis','background','technical','security','performance','other')),
  draft_text TEXT NOT NULL DEFAULT '',
  citation_status TEXT NOT NULL DEFAULT 'planned' CHECK (citation_status IN ('planned','drafted','verified','rejected')),
  UNIQUE (match_id, report_section, citation_purpose)
);

CREATE TABLE IF NOT EXISTS policy_verification_log (
  verification_id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES policy_document(policy_id) ON DELETE CASCADE,
  checked_url TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  check_method TEXT NOT NULL,
  result TEXT NOT NULL,
  previous_status TEXT NOT NULL DEFAULT '',
  current_status TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS document_standard (
  standard_id TEXT PRIMARY KEY,
  document_type TEXT NOT NULL,
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  jurisdiction_name TEXT NOT NULL DEFAULT '',
  authority_name TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  effective_date TEXT NOT NULL DEFAULT '',
  expiry_date TEXT NOT NULL DEFAULT '',
  required_sections_json TEXT NOT NULL DEFAULT '[]',
  optional_sections_json TEXT NOT NULL DEFAULT '[]',
  table_requirements_json TEXT NOT NULL DEFAULT '[]',
  official_url TEXT NOT NULL DEFAULT '',
  source_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired','candidate')),
  UNIQUE (document_type, jurisdiction_code, title, version)
);

CREATE TABLE IF NOT EXISTS format_profile (
  profile_id TEXT PRIMARY KEY,
  standard_id TEXT REFERENCES document_standard(standard_id) ON DELETE SET NULL,
  profile_name TEXT NOT NULL,
  source_document_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  page_setup_json TEXT NOT NULL DEFAULT '{}',
  cover_rules_json TEXT NOT NULL DEFAULT '{}',
  toc_rules_json TEXT NOT NULL DEFAULT '{}',
  numbering_rules_json TEXT NOT NULL DEFAULT '{}',
  header_footer_rules_json TEXT NOT NULL DEFAULT '{}',
  section_rules_json TEXT NOT NULL DEFAULT '{}',
  profile_hash TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.0,
  review_status TEXT NOT NULL DEFAULT 'candidate' CHECK (review_status IN ('candidate','confirmed','retired')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS style_rule (
  style_rule_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES format_profile(profile_id) ON DELETE CASCADE,
  semantic_role TEXT NOT NULL,
  style_id TEXT NOT NULL DEFAULT '',
  style_name TEXT NOT NULL DEFAULT '',
  outline_level INTEGER,
  numbering_level INTEGER,
  based_on_style_id TEXT NOT NULL DEFAULT '',
  font_east_asia TEXT NOT NULL DEFAULT '',
  font_latin TEXT NOT NULL DEFAULT '',
  font_size_pt REAL,
  bold INTEGER NOT NULL DEFAULT 0 CHECK (bold IN (0,1)),
  alignment TEXT NOT NULL DEFAULT '',
  spacing_before_pt REAL,
  spacing_after_pt REAL,
  line_spacing TEXT NOT NULL DEFAULT '',
  first_line_indent_chars REAL,
  left_indent_chars REAL,
  allow_direct_formatting INTEGER NOT NULL DEFAULT 0 CHECK (allow_direct_formatting IN (0,1)),
  raw_style_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (profile_id, semantic_role)
);

CREATE TABLE IF NOT EXISTS project_document_profile (
  project_profile_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES format_profile(profile_id) ON DELETE RESTRICT,
  match_score REAL NOT NULL,
  match_reason TEXT NOT NULL,
  decision_status TEXT NOT NULL DEFAULT 'needs_confirmation'
    CHECK (decision_status IN ('ai_recommended','user_confirmed','user_rejected','needs_confirmation')),
  selected_at TEXT,
  UNIQUE (project_id, profile_id)
);

CREATE TABLE IF NOT EXISTS format_lint_run (
  lint_run_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES project(project_id) ON DELETE CASCADE,
  source_document_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  profile_id TEXT REFERENCES format_profile(profile_id) ON DELETE SET NULL,
  file_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  issue_count INTEGER NOT NULL DEFAULT 0,
  blocking_count INTEGER NOT NULL DEFAULT 0,
  checked_at TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS format_lint_issue (
  lint_issue_id TEXT PRIMARY KEY,
  lint_run_id TEXT NOT NULL REFERENCES format_lint_run(lint_run_id) ON DELETE CASCADE,
  paragraph_index INTEGER,
  table_index INTEGER,
  issue_type TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('blocking','high','medium','low')),
  style_id TEXT NOT NULL DEFAULT '',
  text_excerpt TEXT NOT NULL DEFAULT '',
  actual_json TEXT NOT NULL DEFAULT '{}',
  expected_json TEXT NOT NULL DEFAULT '{}',
  auto_fixable INTEGER NOT NULL DEFAULT 0 CHECK (auto_fixable IN (0,1)),
  resolution_status TEXT NOT NULL DEFAULT 'open' CHECK (resolution_status IN ('open','fixed','accepted','ignored'))
);

CREATE TABLE IF NOT EXISTS corpus_document (
  corpus_document_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source_document(source_id) ON DELETE CASCADE,
  document_type TEXT NOT NULL,
  jurisdiction_code TEXT NOT NULL DEFAULT '',
  project_type TEXT NOT NULL DEFAULT '',
  quality_level TEXT NOT NULL CHECK (quality_level IN ('A','B','C','D')),
  permission_scope TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','retired','prohibited')),
  version TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_block (
  block_id TEXT PRIMARY KEY,
  corpus_document_id TEXT NOT NULL REFERENCES corpus_document(corpus_document_id) ON DELETE CASCADE,
  source_location TEXT NOT NULL,
  section_role TEXT NOT NULL,
  module_code TEXT NOT NULL DEFAULT '',
  clean_text TEXT NOT NULL,
  reuse_class TEXT NOT NULL CHECK (reuse_class IN ('A','B','C','D')),
  quality_level TEXT NOT NULL CHECK (quality_level IN ('A','B','C','D')),
  applicable_document_types_json TEXT NOT NULL DEFAULT '[]',
  applicable_project_types_json TEXT NOT NULL DEFAULT '[]',
  prerequisites_json TEXT NOT NULL DEFAULT '[]',
  variable_slots_json TEXT NOT NULL DEFAULT '[]',
  forbidden_terms_json TEXT NOT NULL DEFAULT '[]',
  length_band TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','retired','prohibited')),
  text_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_tag (
  tag_id TEXT PRIMARY KEY,
  tag_group TEXT NOT NULL,
  tag_code TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  parent_tag_id TEXT REFERENCES corpus_tag(tag_id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  UNIQUE (tag_group, tag_code)
);

CREATE TABLE IF NOT EXISTS corpus_block_tag (
  block_id TEXT NOT NULL REFERENCES corpus_block(block_id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES corpus_tag(tag_id) ON DELETE CASCADE,
  PRIMARY KEY (block_id, tag_id)
);

CREATE TABLE IF NOT EXISTS product_capability (
  capability_id TEXT PRIMARY KEY,
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  capability_name TEXT NOT NULL,
  capability_description TEXT NOT NULL,
  prerequisites_json TEXT NOT NULL DEFAULT '[]',
  interface_dependencies_json TEXT NOT NULL DEFAULT '[]',
  exclusions_json TEXT NOT NULL DEFAULT '[]',
  applicable_versions_json TEXT NOT NULL DEFAULT '[]',
  standard_block_ids_json TEXT NOT NULL DEFAULT '[]',
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','retired')),
  UNIQUE (product_code, capability_name)
);

CREATE TABLE IF NOT EXISTS project_scope_item (
  scope_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  source_id TEXT REFERENCES source_document(source_id) ON DELETE SET NULL,
  original_name TEXT NOT NULL,
  standard_name TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  item_type TEXT NOT NULL DEFAULT '',
  construction_mode TEXT NOT NULL DEFAULT 'pending_confirmation',
  quantity REAL,
  unit TEXT NOT NULL DEFAULT '',
  customer_scope INTEGER NOT NULL DEFAULT 1 CHECK (customer_scope IN (0,1)),
  investment_category TEXT NOT NULL DEFAULT '',
  acceptance_target TEXT NOT NULL DEFAULT '',
  chapter_location TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending_confirmation',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scope_product_map (
  map_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  scope_id TEXT NOT NULL REFERENCES project_scope_item(scope_id) ON DELETE CASCADE,
  capability_id TEXT NOT NULL REFERENCES product_capability(capability_id) ON DELETE RESTRICT,
  mapping_type TEXT NOT NULL CHECK (mapping_type IN ('one_to_one','one_to_many','many_to_one','partial','gap','out_of_scope')),
  coverage_note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','conflict','missing','ignored')),
  confidence REAL NOT NULL DEFAULT 0.0,
  review_note TEXT NOT NULL DEFAULT '',
  reviewed_by TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (scope_id, capability_id)
);

CREATE TABLE IF NOT EXISTS section_blueprint (
  blueprint_id TEXT PRIMARY KEY,
  document_type TEXT NOT NULL,
  section_role TEXT NOT NULL,
  purpose TEXT NOT NULL,
  required_questions_json TEXT NOT NULL DEFAULT '[]',
  required_fact_categories_json TEXT NOT NULL DEFAULT '[]',
  required_scope_types_json TEXT NOT NULL DEFAULT '[]',
  required_policy_topics_json TEXT NOT NULL DEFAULT '[]',
  required_tables_json TEXT NOT NULL DEFAULT '[]',
  length_min INTEGER,
  length_max INTEGER,
  forbidden_content_json TEXT NOT NULL DEFAULT '[]',
  completion_rules_json TEXT NOT NULL DEFAULT '[]',
  review_status TEXT NOT NULL DEFAULT 'approved' CHECK (review_status IN ('pending','approved','retired')),
  UNIQUE (document_type, section_role)
);

CREATE TABLE IF NOT EXISTS section_composition_plan (
  plan_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  chapter_code TEXT NOT NULL,
  section_title TEXT NOT NULL,
  blueprint_id TEXT REFERENCES section_blueprint(blueprint_id) ON DELETE SET NULL,
  purpose TEXT NOT NULL,
  conclusion_boundary TEXT NOT NULL,
  required_questions_json TEXT NOT NULL DEFAULT '[]',
  length_min INTEGER,
  length_max INTEGER,
  required_tables_json TEXT NOT NULL DEFAULT '[]',
  forbidden_content_json TEXT NOT NULL DEFAULT '[]',
  completion_rules_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ready','blocked','completed')),
  version_no INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, chapter_code, version_no)
);

CREATE TABLE IF NOT EXISTS section_plan_source (
  plan_source_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES section_composition_plan(plan_id) ON DELETE CASCADE,
  source_type TEXT NOT NULL CHECK (source_type IN ('fact','scope','policy','corpus','capability','indicator','reference')),
  source_object_id TEXT NOT NULL,
  usage_mode TEXT NOT NULL CHECK (usage_mode IN ('direct','parameterized','structure_only','evidence','prohibited')),
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE (plan_id, source_type, source_object_id)
);

CREATE TABLE IF NOT EXISTS draft_section_version (
  draft_version_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES section_composition_plan(plan_id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('ai','manual','restored')),
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','needs_review','adopted','discarded','failed')),
  check_result_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (plan_id, version_no)
);

CREATE TABLE IF NOT EXISTS validation_run (
  validation_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  validation_type TEXT NOT NULL,
  baseline_version TEXT NOT NULL DEFAULT 'working',
  status TEXT NOT NULL CHECK (status IN ('passed','warning','failed')),
  issue_count INTEGER NOT NULL DEFAULT 0,
  blocking_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS validation_issue (
  issue_id TEXT PRIMARY KEY,
  validation_run_id TEXT NOT NULL REFERENCES validation_run(validation_run_id) ON DELETE CASCADE,
  severity TEXT NOT NULL CHECK (severity IN ('blocking','high','medium','low')),
  issue_type TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL,
  related_ids_json TEXT NOT NULL DEFAULT '[]',
  suggested_action TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','accepted','ignored')),
  resolution TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS llm_call_log (
  llm_call_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES project(project_id) ON DELETE CASCADE,
  stage_code TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  output_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  error_message TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES project(project_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_value_json TEXT NOT NULL DEFAULT '{}',
  after_value_json TEXT NOT NULL DEFAULT '{}',
  operator TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_document_project ON source_document(project_id, source_class);
CREATE INDEX IF NOT EXISTS idx_fact_project_status ON project_fact(project_id, fact_status, materiality);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence_record(source_id);
CREATE INDEX IF NOT EXISTS idx_inference_project_status ON inference_record(project_id, status, materiality);
CREATE INDEX IF NOT EXISTS idx_question_project_status ON confirmation_question(project_id, status, materiality);
CREATE INDEX IF NOT EXISTS idx_confirmation_target ON confirmation_record(project_id, target_type, target_id, confirmed_at);
CREATE INDEX IF NOT EXISTS idx_policy_jurisdiction ON policy_document(jurisdiction_level, jurisdiction_code, validity_status);
CREATE INDEX IF NOT EXISTS idx_policy_clause_policy ON policy_clause(policy_id, requirement_type);
CREATE INDEX IF NOT EXISTS idx_policy_match_project_order ON project_policy_match(project_id, basis_use, basis_order, sort_key);
CREATE INDEX IF NOT EXISTS idx_style_rule_profile ON style_rule(profile_id, semantic_role);
CREATE INDEX IF NOT EXISTS idx_format_lint_issue_run ON format_lint_issue(lint_run_id, severity, issue_type);
CREATE INDEX IF NOT EXISTS idx_corpus_block_role ON corpus_block(section_role, module_code, reuse_class, review_status);
CREATE INDEX IF NOT EXISTS idx_scope_project_status ON project_scope_item(project_id, status);
CREATE INDEX IF NOT EXISTS idx_scope_product_project ON scope_product_map(project_id, status);
CREATE INDEX IF NOT EXISTS idx_plan_project_chapter ON section_composition_plan(project_id, chapter_code, status);
CREATE INDEX IF NOT EXISTS idx_validation_issue_run ON validation_issue(validation_run_id, severity, status);

CREATE TRIGGER IF NOT EXISTS trg_confirmation_record_no_update
BEFORE UPDATE ON confirmation_record
BEGIN
  SELECT RAISE(ABORT, 'confirmation_record is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_confirmation_record_no_delete
BEFORE DELETE ON confirmation_record
BEGIN
  SELECT RAISE(ABORT, 'confirmation_record is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log is append-only');
END;
