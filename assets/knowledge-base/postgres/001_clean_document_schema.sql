CREATE TABLE IF NOT EXISTS public.feasibility_clean_document (
  document_id TEXT PRIMARY KEY,
  project_code TEXT NOT NULL,
  title TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  source_type TEXT NOT NULL,
  cleaning_version TEXT NOT NULL,
  cleaning_method TEXT NOT NULL,
  contains_personal_data BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (project_code, source_sha256)
);

CREATE TABLE IF NOT EXISTS public.feasibility_clean_document_block (
  block_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES public.feasibility_clean_document(document_id) ON DELETE CASCADE,
  block_index INTEGER NOT NULL CHECK (block_index > 0),
  block_type TEXT NOT NULL CHECK (block_type IN ('heading', 'paragraph', 'table')),
  heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_location TEXT NOT NULL,
  clean_text TEXT NOT NULL,
  clean_text_sha256 TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, block_index)
);

CREATE INDEX IF NOT EXISTS idx_feasibility_clean_document_project
  ON public.feasibility_clean_document(project_code, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_feasibility_clean_document_block_document
  ON public.feasibility_clean_document_block(document_id, block_index);
