// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! Index de contenu (table `code_content`, migration 004) et signatures
//! persistantes (`code_examples.file_mtime`).
//!
//! Tout est facultatif et rétro-compatible : si la migration n'a pas été
//! appliquée (ou si `CODECENTER_CONTENT_INDEX=off`), le moteur retombe sur la
//! lecture des fichiers sur le disque, exactement comme avant la 1.0.4.

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use sqlx::{Error, PgPool, Row};
use uuid::Uuid;

use super::get_pool;

/// Fichiers plus gros que cette taille ne sont pas copiés dans l'index de
/// contenu (ils restent lus sur le disque, avec les plafonds de la recherche).
pub const MAX_INDEXED_BYTES: usize = 1024 * 1024;

/// Après un échec « table absente », on ne retente qu'au bout de ce délai.
const RETRY_AFTER: Duration = Duration::from_secs(300);

static UNAVAILABLE_SINCE: Mutex<Option<Instant>> = Mutex::new(None);

/// `CODECENTER_CONTENT_INDEX=off` désactive l'index de contenu.
pub fn enabled_by_configuration() -> bool {
    !matches!(
        std::env::var("CODECENTER_CONTENT_INDEX")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "off" | "0" | "false" | "no" | "non"
    )
}

fn temporarily_unavailable() -> bool {
    let guard = UNAVAILABLE_SINCE.lock().unwrap_or_else(|e| e.into_inner());
    guard.is_some_and(|since| since.elapsed() < RETRY_AFTER)
}

fn mark_unavailable() {
    let mut guard = UNAVAILABLE_SINCE.lock().unwrap_or_else(|e| e.into_inner());
    if guard.is_none() {
        eprintln!(
            "CodeCenter : index de contenu indisponible (migration 004 non appliquée ?) ; \
             lecture des fichiers sur le disque."
        );
    }
    *guard = Some(Instant::now());
}

fn mark_available() {
    *UNAVAILABLE_SINCE.lock().unwrap_or_else(|e| e.into_inner()) = None;
}

/// Erreurs « objet absent » (table ou colonne) : migration non appliquée.
fn is_missing_schema(error: &Error) -> bool {
    matches!(
        error,
        Error::Database(db) if matches!(db.code().as_deref(), Some("42P01") | Some("42703"))
    )
}

/// Vrai si l'index de contenu est utilisable maintenant.
pub async fn available(pool: &PgPool) -> bool {
    if !enabled_by_configuration() || temporarily_unavailable() {
        return false;
    }
    match sqlx::query_scalar::<_, bool>(
        "SELECT to_regclass('code_content') IS NOT NULL",
    )
    .fetch_one(pool)
    .await
    {
        Ok(true) => {
            mark_available();
            true
        }
        Ok(false) => {
            mark_unavailable();
            false
        }
        Err(_) => false,
    }
}

/// Texte tel qu'il est stocké : minuscules (comme la recherche), sans octet nul
/// (interdit par PostgreSQL).
pub fn normalize(content: &[u8]) -> String {
    String::from_utf8_lossy(content)
        .to_lowercase()
        .replace('\0', "")
}

/// Mémorise le contenu et la date de modification d'un document déjà enregistré.
pub async fn store_content(
    storage_path: &str,
    content_sha256: &str,
    content: &[u8],
    mtime: i64,
) -> Result<(), Error> {
    if !enabled_by_configuration() || temporarily_unavailable() {
        return Ok(());
    }

    let pool = get_pool().await?;
    let lowered = if content.len() <= MAX_INDEXED_BYTES {
        Some(normalize(content))
    } else {
        None
    };

    let result = sqlx::query(
        r#"
        WITH doc AS (
            SELECT id FROM code_examples WHERE storage_path = $1
        ),
        touched AS (
            UPDATE code_examples
            SET file_mtime = $4
            WHERE storage_path = $1 AND file_mtime IS DISTINCT FROM $4
            RETURNING id
        )
        INSERT INTO code_content (code_example_id, content_lower, content_sha256)
        SELECT id, $2, $3 FROM doc WHERE $2 IS NOT NULL
        ON CONFLICT (code_example_id) DO UPDATE SET
            content_lower = EXCLUDED.content_lower,
            content_sha256 = EXCLUDED.content_sha256,
            indexed_at = NOW()
        WHERE code_content.content_sha256 IS DISTINCT FROM EXCLUDED.content_sha256
        "#,
    )
    .bind(storage_path)
    .bind(lowered.as_deref())
    .bind(content_sha256)
    .bind(mtime)
    .execute(pool)
    .await;

    match result {
        Ok(_) => Ok(()),
        Err(error) if is_missing_schema(&error) => {
            mark_unavailable();
            Ok(())
        }
        Err(error) => Err(error),
    }
}

/// Signatures (chemin, date, taille) des fichiers dont le contenu est déjà
/// indexé : ils n'auront pas besoin d'être relus après un redémarrage.
pub async fn load_signatures() -> Vec<(PathBuf, u64, i64)> {
    let Ok(pool) = get_pool().await else {
        return Vec::new();
    };
    if !available(pool).await {
        return Vec::new();
    }

    let rows = sqlx::query(
        r#"
        SELECT ce.storage_path, ce.file_mtime, ce.file_size
        FROM code_examples ce
        JOIN code_content cc ON cc.code_example_id = ce.id
        WHERE ce.file_mtime IS NOT NULL
        "#,
    )
    .fetch_all(pool)
    .await;

    match rows {
        Ok(rows) => rows
            .into_iter()
            .filter_map(|row| {
                let path: String = row.try_get("storage_path").ok()?;
                let mtime: i64 = row.try_get("file_mtime").ok()?;
                let size: i64 = row.try_get("file_size").ok()?;
                Some((PathBuf::from(path), u64::try_from(mtime).ok()?, size))
            })
            .collect(),
        Err(_) => Vec::new(),
    }
}

/// Identifiants (parmi `ids`) dont le contenu contient au moins un des motifs.
pub async fn matching_ids(
    pool: &PgPool,
    ids: &[Uuid],
    patterns: &[String],
) -> Result<Vec<Uuid>, Error> {
    if ids.is_empty() || patterns.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_scalar::<_, Uuid>(
        r#"
        SELECT code_example_id
        FROM code_content
        WHERE code_example_id = ANY($1) AND content_lower LIKE ANY($2)
        "#,
    )
    .bind(ids)
    .bind(patterns)
    .fetch_all(pool)
    .await
}

/// Contenu (minuscules) d'un lot de documents.
pub async fn contents(
    pool: &PgPool,
    ids: &[Uuid],
) -> Result<Vec<(Uuid, String)>, Error> {
    let rows = sqlx::query(
        "SELECT code_example_id, content_lower FROM code_content WHERE code_example_id = ANY($1)",
    )
    .bind(ids)
    .fetch_all(pool)
    .await?;

    rows.into_iter()
        .map(|row| Ok((row.try_get("code_example_id")?, row.try_get("content_lower")?)))
        .collect()
}

/// Échappe un terme pour `LIKE` (`%`, `_` et `\` sont littéraux).
pub fn like_contains(term: &str) -> String {
    let mut pattern = String::with_capacity(term.len() + 2);
    pattern.push('%');
    for c in term.chars() {
        if matches!(c, '%' | '_' | '\\') {
            pattern.push('\\');
        }
        pattern.push(c);
    }
    pattern.push('%');
    pattern
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn like_patterns_escape_wildcards() {
        assert_eq!(like_contains("parse_json"), "%parse\\_json%");
        assert_eq!(like_contains("100%"), "%100\\%%");
        assert_eq!(like_contains("a\\b"), "%a\\\\b%");
    }

    #[test]
    fn normalize_lowercases_and_strips_nul() {
        assert_eq!(normalize("ÉCOLE\0Test".as_bytes()), "écoletest");
        assert_eq!(normalize(&[0xff, b'A']), "\u{fffd}a");
    }

    #[test]
    fn configuration_switch() {
        // SAFETY : variable propre à ce test.
        unsafe { std::env::set_var("CODECENTER_CONTENT_INDEX", "off") };
        assert!(!enabled_by_configuration());
        unsafe { std::env::remove_var("CODECENTER_CONTENT_INDEX") };
        assert!(enabled_by_configuration());
    }
}
