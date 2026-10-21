-- 1.0.4 : index de contenu et signatures persistantes. Migration ADDITIVE :
-- rien n'est supprimé ni modifié, l'ancien moteur continue de fonctionner.
--
-- code_content : texte du fichier en minuscules (le moteur lit alors la base
-- au lieu du disque pour noter les résultats : crucial sur un disque dur).
-- code_examples.file_mtime : date de modification (secondes) vue à l'indexation,
-- pour ne pas relire les fichiers inchangés après un redémarrage.

ALTER TABLE code_examples
    ADD COLUMN IF NOT EXISTS file_mtime BIGINT;

CREATE TABLE IF NOT EXISTS code_content (
    code_example_id UUID PRIMARY KEY
        REFERENCES code_examples(id) ON DELETE CASCADE,
    content_lower TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index trigramme : recherche de sous-chaînes rapide. Si l'extension pg_trgm
-- est indisponible (droits, paquet manquant), la recherche fonctionne quand
-- même, sans cet index.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_code_content_trgm
        ON code_content USING gin (content_lower gin_trgm_ops);
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_trgm indisponible, index trigramme non créé : %', SQLERRM;
END
$$;
