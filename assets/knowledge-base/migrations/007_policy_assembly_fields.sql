ALTER TABLE policy_clause ADD COLUMN permitted_sections_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE policy_clause ADD COLUMN forbidden_claims_json TEXT NOT NULL DEFAULT '[]';
