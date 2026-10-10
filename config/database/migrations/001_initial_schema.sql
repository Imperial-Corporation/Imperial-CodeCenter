CREATE TABLE IF NOT EXISTS categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS languages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    file_extensions TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_url TEXT,
    repository_name TEXT,
    revision TEXT,
    license TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_url, revision)
);

CREATE TABLE IF NOT EXISTS code_examples (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    language_id BIGINT REFERENCES languages(id),
    source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    storage_path TEXT NOT NULL UNIQUE,
    original_filename TEXT,
    content_sha256 TEXT NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0 CHECK (file_size >= 0),
    analysis_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (analysis_status IN ('pending', 'processing', 'completed', 'failed')),
    analysis_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS code_example_categories (
    code_example_id UUID NOT NULL
        REFERENCES code_examples(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL
        REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (code_example_id, category_id)
);

CREATE TABLE IF NOT EXISTS code_analysis (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_example_id UUID NOT NULL
        REFERENCES code_examples(id) ON DELETE CASCADE,
    analyzer_version TEXT NOT NULL,
    summary TEXT,
    symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (code_example_id, analyzer_version)
);

CREATE TABLE IF NOT EXISTS indexing_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    items_total BIGINT NOT NULL DEFAULT 0,
    items_processed BIGINT NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_code_examples_language
    ON code_examples(language_id);

CREATE INDEX IF NOT EXISTS idx_code_examples_analysis_status
    ON code_examples(analysis_status);

CREATE INDEX IF NOT EXISTS idx_code_examples_updated_at
    ON code_examples(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_code_analysis_example
    ON code_analysis(code_example_id);

CREATE INDEX IF NOT EXISTS idx_indexing_jobs_status
    ON indexing_jobs(status);