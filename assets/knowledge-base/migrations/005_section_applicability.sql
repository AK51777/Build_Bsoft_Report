ALTER TABLE section_composition_plan
  ADD COLUMN applicability_status TEXT NOT NULL DEFAULT 'applicable'
  CHECK (applicability_status IN ('applicable','not_applicable','pending_confirmation'));

ALTER TABLE section_composition_plan
  ADD COLUMN applicability_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_section_plan_project_applicability
  ON section_composition_plan(project_id, applicability_status, chapter_code);
