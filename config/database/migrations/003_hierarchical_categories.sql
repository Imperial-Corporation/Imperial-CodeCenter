-- Ajoute une hiérarchie de catégories sans profondeur maximale.
ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS parent_id BIGINT
    REFERENCES categories(id) ON DELETE CASCADE;

-- Les catégories peuvent porter le même nom dans des branches différentes.
ALTER TABLE categories
    DROP CONSTRAINT IF EXISTS categories_name_key;

ALTER TABLE categories
    DROP CONSTRAINT IF EXISTS categories_slug_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_root_slug
    ON categories (slug)
    WHERE parent_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_child_slug
    ON categories (parent_id, slug)
    WHERE parent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_categories_parent
    ON categories (parent_id);

-- Index utiles pour les recherches par empreinte et les relations de tags.
CREATE INDEX IF NOT EXISTS idx_code_examples_sha256
    ON code_examples (content_sha256);

CREATE INDEX IF NOT EXISTS idx_code_example_categories_category
    ON code_example_categories (category_id, code_example_id);

-- Référentiel initial des langages.
INSERT INTO languages (name, slug, file_extensions)
VALUES
    ('C',          'c',          ARRAY['c', 'h']),
    ('C++',        'cpp',        ARRAY['cc', 'cpp', 'cxx', 'hh', 'hpp', 'hxx']),
    ('Rust',       'rust',       ARRAY['rs']),
    ('Python',     'python',     ARRAY['py', 'pyi']),
    ('JavaScript', 'javascript', ARRAY['js', 'jsx', 'mjs', 'cjs']),
    ('TypeScript', 'typescript', ARRAY['ts', 'tsx', 'mts', 'cts']),
    ('Java',       'java',       ARRAY['java']),
    ('C#',         'csharp',     ARRAY['cs']),
    ('Go',         'go',         ARRAY['go']),
    ('Ruby',       'ruby',       ARRAY['rb']),
    ('PHP',        'php',        ARRAY['php']),
    ('Swift',      'swift',      ARRAY['swift']),
    ('Kotlin',     'kotlin',     ARRAY['kt', 'kts']),
    ('Scala',      'scala',      ARRAY['scala']),
    ('Shell',      'shell',      ARRAY['sh', 'bash']),
    ('HTML',       'html',       ARRAY['html', 'htm']),
    ('CSS',        'css',        ARRAY['css']),
    ('SQL',        'sql',        ARRAY['sql']),
    ('Lua',        'lua',        ARRAY['lua']),
    ('Zig',        'zig',        ARRAY['zig'])
ON CONFLICT (slug) DO UPDATE
SET name = EXCLUDED.name,
    file_extensions = EXCLUDED.file_extensions;
