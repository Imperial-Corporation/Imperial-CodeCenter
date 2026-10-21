// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! Recherche et lecture de documents indexés, exposées à Python.
//!
//! Le contenu des fichiers n'est pas stocké en base : PostgreSQL fournit
//! les métadonnées et les filtres, le contenu est lu sur le disque, dans
//! les dossiers surveillés uniquement, avec des plafonds stricts.

pub mod paths;
pub mod text;

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::time::Instant;

use serde::Serialize;
use sqlx::Row;
use uuid::Uuid;

use crate::database::{content, get_pool, runtime};
use crate::indexing::fingerprint::hash_content;

use paths::{relative_display, resolve_in_roots, watch_roots, PathError};

/// Candidats maximum chargés depuis PostgreSQL pour une recherche.
const MAX_CANDIDATES: i64 = 5000;
/// Fichiers dont le contenu est analysé au maximum par recherche.
const MAX_SCAN_FILES: usize = 3000;
/// Volume maximal lu sur le disque par recherche (96 Mio).
const MAX_SCAN_BYTES: u64 = 96 * 1024 * 1024;
/// Taille maximale d'un fichier analysé (1 Mio).
const MAX_FILE_BYTES: u64 = 1024 * 1024;
/// Résultats maximum renvoyés.
const MAX_LIMIT: usize = 50;
/// Octets de contenu retournés par défaut / au plus par lecture.
const DEFAULT_READ_BYTES: usize = 65_536;
const HARD_READ_BYTES: usize = 262_144;

/// Erreurs typées, traduites en exceptions Python distinctes.
#[derive(Debug)]
pub enum SearchError {
    Invalid(String),
    NotFound(String),
    Forbidden(String),
    Internal(String),
}

impl fmt::Display for SearchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SearchError::Invalid(m)
            | SearchError::NotFound(m)
            | SearchError::Forbidden(m)
            | SearchError::Internal(m) => write!(f, "{m}"),
        }
    }
}

impl From<sqlx::Error> for SearchError {
    fn from(error: sqlx::Error) -> Self {
        SearchError::Internal(format!("Erreur PostgreSQL : {error}"))
    }
}

impl From<PathError> for SearchError {
    fn from(error: PathError) -> Self {
        match error {
            PathError::Outside => SearchError::Forbidden(error.to_string()),
            PathError::Config(_) => SearchError::Internal(error.to_string()),
            PathError::NotFile(_) => SearchError::NotFound(error.to_string()),
        }
    }
}

#[derive(Debug, Serialize)]
struct Hit {
    document_id: String,
    title: String,
    relative_path: String,
    language: String,
    language_name: String,
    categories: Vec<String>,
    score: f64,
    file_size: i64,
    content_sha256: String,
    summary: Option<String>,
    line_start: usize,
    line_end: usize,
    snippet: String,
    content_matched: bool,
}

#[derive(Debug, Serialize)]
struct SearchOutput {
    took_ms: u64,
    terms: Vec<String>,
    candidates: usize,
    scanned_files: usize,
    /// Vrai si un plafond a empêché d'analyser tous les candidats.
    truncated: bool,
    results: Vec<Hit>,
}

struct Candidate {
    id: Uuid,
    title: String,
    storage_path: String,
    file_size: i64,
    sha256: String,
    has_content: bool,
    language: String,
    language_name: String,
    categories: Vec<String>,
    summary: Option<String>,
    meta_score: f64,
}

/// Normalise un filtre : vide => absent, sinon minuscules bornées.
fn clean_filter(value: Option<&str>) -> Result<Option<String>, SearchError> {
    match value.map(str::trim).filter(|v| !v.is_empty()) {
        None => Ok(None),
        Some(v) if v.chars().count() > 100 => {
            Err(SearchError::Invalid("Filtre trop long.".into()))
        }
        Some(v) => Ok(Some(v.to_lowercase())),
    }
}

/// Recherche des documents (filtres langage et catégorie optionnels).
pub fn search_json(
    query: &str,
    limit: usize,
    language: Option<&str>,
    category: Option<&str>,
) -> Result<String, SearchError> {
    let query = query.trim();

    if query.is_empty() {
        return Err(SearchError::Invalid("La requête est vide.".into()));
    }

    if query.chars().count() > 2000 {
        return Err(SearchError::Invalid("La requête est trop longue.".into()));
    }

    let limit = limit.clamp(1, MAX_LIMIT);
    let language = clean_filter(language)?;
    let category = clean_filter(category)?;

    let output = runtime().block_on(run_search(query, limit, language, category))?;

    serde_json::to_string(&output)
        .map_err(|error| SearchError::Internal(error.to_string()))
}

async fn run_search(
    query: &str,
    limit: usize,
    language: Option<String>,
    category: Option<String>,
) -> Result<SearchOutput, SearchError> {
    let started = Instant::now();
    let terms = text::tokenize(query);

    if terms.is_empty() {
        return Ok(SearchOutput {
            took_ms: 0,
            terms,
            candidates: 0,
            scanned_files: 0,
            truncated: false,
            results: Vec::new(),
        });
    }

    let roots = watch_roots()?;
    let pool = get_pool().await?;

    // Index de contenu (migration 004) : le contenu est noté depuis PostgreSQL au
    // lieu d'être relu sur le disque ; sans lui, comportement d'avant la 1.0.4.
    let use_index = content::available(pool).await;
    let (content_join, content_flag, content_group) = if use_index {
        (
            "LEFT JOIN code_content cc ON cc.code_example_id = ce.id",
            "(cc.code_example_id IS NOT NULL)",
            ", cc.code_example_id",
        )
    } else {
        ("", "FALSE", "")
    };

    let sql = format!(
        r#"
        SELECT
            ce.id,
            ce.title,
            ce.storage_path,
            ce.file_size,
            ce.content_sha256,
            {content_flag} AS has_content,
            COALESCE(l.slug, '')  AS language,
            COALESCE(l.name, '')  AS language_name,
            COALESCE(
                ARRAY_AGG(DISTINCT c.name) FILTER (WHERE c.id IS NOT NULL),
                '{{}}'
            ) AS categories,
            (
                SELECT ca.summary
                FROM code_analysis ca
                WHERE ca.code_example_id = ce.id
                ORDER BY ca.analyzed_at DESC
                LIMIT 1
            ) AS summary
        FROM code_examples ce
        LEFT JOIN languages l ON l.id = ce.language_id
        LEFT JOIN code_example_categories cec ON cec.code_example_id = ce.id
        LEFT JOIN categories c ON c.id = cec.category_id
        {content_join}
        WHERE ($1::TEXT IS NULL OR l.slug = $1)
          AND (
              $2::TEXT IS NULL OR EXISTS (
                  SELECT 1
                  FROM code_example_categories x
                  JOIN categories xc ON xc.id = x.category_id
                  WHERE x.code_example_id = ce.id AND xc.slug = $2
              )
          )
        GROUP BY ce.id, l.slug, l.name{content_group}
        ORDER BY ce.updated_at DESC
        LIMIT $3
        "#
    );

    let rows = sqlx::query(&sql)
        .bind(language.as_deref())
        .bind(category.as_deref())
        .bind(MAX_CANDIDATES)
        .fetch_all(pool)
        .await?;

    let mut candidates: Vec<Candidate> = Vec::with_capacity(rows.len());

    for row in rows {
        let categories: Vec<String> = row.try_get("categories")?;
        let title: String = row.try_get("title")?;
        let storage_path: String = row.try_get("storage_path")?;
        let language: String = row.try_get("language")?;
        let summary: Option<String> = row.try_get("summary")?;

        let meta_score = text::metadata_score(
            &terms,
            &title,
            &storage_path,
            &categories,
            &language,
            summary.as_deref().unwrap_or(""),
        );

        candidates.push(Candidate {
            id: row.try_get("id")?,
            title,
            storage_path,
            file_size: row.try_get("file_size")?,
            sha256: row.try_get("content_sha256")?,
            has_content: row.try_get("has_content")?,
            language,
            language_name: row.try_get("language_name")?,
            categories,
            summary,
            meta_score,
        });
    }

    let total_candidates = candidates.len();

    // Les candidats déjà proches par leurs métadonnées sont lus en premier :
    // si un plafond est atteint, les meilleurs ont été examinés.
    candidates.sort_by(|a, b| {
        b.meta_score
            .partial_cmp(&a.meta_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let max_scan_bytes = max_scan_bytes();
    let mut scanned_files = 0usize;
    let mut scanned_bytes = 0u64;
    let mut truncated = false;

    // --- Contenu déjà dans l'index : notation par lots, sans toucher au disque ---
    let mut indexed_points: HashMap<Uuid, f64> = HashMap::new();
    if use_index {
        let indexed_ids: Vec<Uuid> = candidates
            .iter()
            .filter(|c| c.has_content && c.file_size >= 0 && (c.file_size as u64) <= MAX_FILE_BYTES)
            .map(|c| c.id)
            .collect();
        let patterns: Vec<String> = terms.iter().map(|t| content::like_contains(t)).collect();
        let matched: HashSet<Uuid> = content::matching_ids(pool, &indexed_ids, &patterns)
            .await?
            .into_iter()
            .collect();

        // Dans l'ordre des candidats : si un plafond est atteint, les meilleurs sont notés.
        let ordered: Vec<Uuid> = candidates
            .iter()
            .filter(|c| matched.contains(&c.id))
            .map(|c| c.id)
            .collect();

        'batches: for batch in ordered.chunks(100) {
            for (id, lowered) in content::contents(pool, batch).await? {
                if scanned_files >= MAX_SCAN_FILES || scanned_bytes >= max_scan_bytes {
                    truncated = true;
                    break 'batches;
                }
                scanned_files += 1;
                scanned_bytes += lowered.len() as u64;
                let points = text::content_score(&terms, query, &lowered);
                if points > 0.0 {
                    indexed_points.insert(id, points);
                }
            }
        }
    }

    // `path` : renseigné quand l'extrait reste à lire sur le disque (résultats retenus seulement).
    let mut pending: Vec<(Hit, Option<std::path::PathBuf>)> = Vec::new();

    for candidate in candidates {
        let mut score = candidate.meta_score;
        let mut snippet = None;
        let mut content_matched = false;
        let mut deferred = false;

        let readable = candidate.file_size >= 0
            && (candidate.file_size as u64) <= MAX_FILE_BYTES;

        if use_index && candidate.has_content && readable {
            if let Some(points) = indexed_points.get(&candidate.id) {
                score += points;
                content_matched = true;
            }
            deferred = score > 0.0;
        } else if readable {
            if scanned_files >= MAX_SCAN_FILES || scanned_bytes >= max_scan_bytes {
                truncated = true;
            } else if let Ok((path, _)) =
                resolve_in_roots(&candidate.storage_path, &roots)
            {
                if let Ok(bytes) = std::fs::read(&path) {
                    scanned_files += 1;
                    scanned_bytes += bytes.len() as u64;

                    let content = String::from_utf8_lossy(&bytes);
                    let lowered = content.to_lowercase();
                    let content_points =
                        text::content_score(&terms, query, &lowered);

                    if content_points > 0.0 {
                        score += content_points;
                        content_matched = true;
                    }

                    if score > 0.0 {
                        snippet = Some(text::best_snippet(&content, &terms));
                    }
                }
            }
        }

        if score <= 0.0 {
            continue;
        }

        let resolved = resolve_in_roots(&candidate.storage_path, &roots).ok();

        let (snippet_value, path_for_snippet) = match snippet {
            Some(value) => (value, None),
            None => (
                text::Snippet {
                    line_start: 1,
                    line_end: 1,
                    text: String::new(),
                },
                if deferred { resolved.as_ref().map(|(p, _)| p.clone()) } else { None },
            ),
        };

        let relative_path = resolved
            .as_ref()
            .map(|(path, root)| relative_display(path, root))
            .unwrap_or_else(|| candidate.title.clone());

        pending.push((
            Hit {
                document_id: candidate.id.to_string(),
                title: candidate.title,
                relative_path,
                language: candidate.language,
                language_name: candidate.language_name,
                categories: candidate.categories,
                score: (score * 100.0).round() / 100.0,
                file_size: candidate.file_size,
                content_sha256: candidate.sha256,
                summary: candidate.summary,
                line_start: snippet_value.line_start,
                line_end: snippet_value.line_end,
                snippet: snippet_value.text,
                content_matched,
            },
            path_for_snippet,
        ));
    }

    pending.sort_by(|a, b| {
        b.0.score
            .partial_cmp(&a.0.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.title.cmp(&b.0.title))
    });
    pending.truncate(limit);

    // Extraits : seuls les résultats retenus (50 au plus) sont lus sur le disque.
    let mut hits: Vec<Hit> = Vec::with_capacity(pending.len());
    for (mut hit, path) in pending {
        if let Some(path) = path {
            if let Ok(bytes) = std::fs::read(&path) {
                let content = String::from_utf8_lossy(&bytes);
                let snippet = text::best_snippet(&content, &terms);
                hit.line_start = snippet.line_start;
                hit.line_end = snippet.line_end;
                hit.snippet = snippet.text;
            }
        }
        hits.push(hit);
    }

    Ok(SearchOutput {
        took_ms: started.elapsed().as_millis() as u64,
        terms,
        candidates: total_candidates,
        scanned_files,
        truncated,
        results: hits,
    })
}

/// Volume maximal analysé par recherche (octets) : 96 Mio, ou
/// `CODECENTER_SEARCH_SCAN_MB` (profil léger : moins de mémoire).
fn max_scan_bytes() -> u64 {
    std::env::var("CODECENTER_SEARCH_SCAN_MB")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|value| *value >= 1)
        .map(|mb| mb.saturating_mul(1024 * 1024))
        .unwrap_or(MAX_SCAN_BYTES)
}

#[derive(Debug, Serialize)]
struct DocumentOutput {
    document_id: String,
    title: String,
    relative_path: String,
    language: String,
    language_name: String,
    categories: Vec<String>,
    file_size: i64,
    content_sha256: String,
    /// Faux si le fichier a changé sur le disque depuis l'indexation.
    content_matches_index: bool,
    summary: Option<String>,
    symbols: serde_json::Value,
    dependencies: serde_json::Value,
    line_start: usize,
    line_end: usize,
    total_lines: usize,
    truncated: bool,
    content: String,
}

/// Lit un document indexé, éventuellement une plage de lignes.
///
/// `start_line`/`end_line` sont 1-indexés et inclusifs ; `max_bytes`
/// plafonne le contenu retourné (coupé à une frontière de ligne).
pub fn get_document_json(
    document_id: &str,
    start_line: Option<usize>,
    end_line: Option<usize>,
    max_bytes: Option<usize>,
) -> Result<String, SearchError> {
    let id = Uuid::parse_str(document_id.trim())
        .map_err(|_| SearchError::Invalid("Identifiant de document invalide.".into()))?;

    if matches!((start_line, end_line), (Some(s), _) if s == 0)
        || matches!((start_line, end_line), (_, Some(e)) if e == 0)
    {
        return Err(SearchError::Invalid(
            "Les numéros de ligne commencent à 1.".into(),
        ));
    }

    let budget = max_bytes
        .unwrap_or(DEFAULT_READ_BYTES)
        .clamp(1, HARD_READ_BYTES);

    let output = runtime().block_on(read_document(id, start_line, end_line, budget))?;

    serde_json::to_string(&output)
        .map_err(|error| SearchError::Internal(error.to_string()))
}

async fn read_document(
    id: Uuid,
    start_line: Option<usize>,
    end_line: Option<usize>,
    budget: usize,
) -> Result<DocumentOutput, SearchError> {
    let roots = watch_roots()?;
    let pool = get_pool().await?;

    let row = sqlx::query(
        r#"
        SELECT
            ce.title,
            ce.storage_path,
            ce.file_size,
            ce.content_sha256,
            COALESCE(l.slug, '') AS language,
            COALESCE(l.name, '') AS language_name,
            COALESCE(
                ARRAY_AGG(DISTINCT c.name) FILTER (WHERE c.id IS NOT NULL),
                '{}'
            ) AS categories,
            ca.summary,
            COALESCE(ca.symbols, '[]'::jsonb) AS symbols,
            COALESCE(ca.dependencies, '[]'::jsonb) AS dependencies
        FROM code_examples ce
        LEFT JOIN languages l ON l.id = ce.language_id
        LEFT JOIN code_example_categories cec ON cec.code_example_id = ce.id
        LEFT JOIN categories c ON c.id = cec.category_id
        LEFT JOIN LATERAL (
            SELECT summary, symbols, dependencies
            FROM code_analysis
            WHERE code_example_id = ce.id
            ORDER BY analyzed_at DESC
            LIMIT 1
        ) ca ON TRUE
        WHERE ce.id = $1
        GROUP BY ce.id, l.slug, l.name, ca.summary, ca.symbols, ca.dependencies
        "#,
    )
    .bind(id)
    .fetch_optional(pool)
    .await?
    .ok_or_else(|| SearchError::NotFound("Document introuvable.".into()))?;

    let storage_path: String = row.try_get("storage_path")?;
    let (path, root) = resolve_in_roots(&storage_path, &roots)?;

    let bytes = std::fs::read(&path)
        .map_err(|_| SearchError::NotFound("Fichier illisible sur le disque.".into()))?;

    let current_sha = hash_content(&bytes);
    let stored_sha: String = row.try_get("content_sha256")?;

    let content = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = content.lines().collect();
    let total_lines = lines.len();

    let first = start_line.unwrap_or(1).max(1);
    let last = end_line.unwrap_or(total_lines).min(total_lines);

    let (selected, line_start, line_end, truncated) = if total_lines == 0 || first > last {
        (String::new(), first, first.saturating_sub(1), false)
    } else {
        let mut out = String::new();
        let mut taken_end = first - 1;

        for (offset, line) in lines[first - 1..last].iter().enumerate() {
            // +1 pour le saut de ligne ; on s'arrête avant de dépasser.
            if out.len() + line.len() + 1 > budget && !out.is_empty() {
                break;
            }
            out.push_str(line);
            out.push('\n');
            taken_end = first + offset;
        }

        (out, first, taken_end, taken_end < last)
    };

    Ok(DocumentOutput {
        document_id: id.to_string(),
        title: row.try_get("title")?,
        relative_path: relative_display(&path, &root),
        language: row.try_get("language")?,
        language_name: row.try_get("language_name")?,
        categories: row.try_get("categories")?,
        file_size: row.try_get("file_size")?,
        content_matches_index: current_sha == stored_sha,
        content_sha256: stored_sha,
        summary: row.try_get("summary")?,
        symbols: row.try_get("symbols")?,
        dependencies: row.try_get("dependencies")?,
        line_start,
        line_end,
        total_lines,
        truncated,
        content: selected,
    })
}

#[derive(Debug, Serialize)]
struct LanguageItem {
    slug: String,
    name: String,
    documents: i64,
}

#[derive(Debug, Serialize)]
struct CategoryItem {
    slug: String,
    name: String,
    /// Chemin hiérarchique complet, par exemple `web/api`.
    path: String,
    documents: i64,
}

#[derive(Debug, Serialize)]
struct FiltersOutput {
    languages: Vec<LanguageItem>,
    categories: Vec<CategoryItem>,
}

/// Langages et catégories réellement présents, utiles comme filtres.
pub fn list_filters_json() -> Result<String, SearchError> {
    let output = runtime().block_on(async {
        let pool = get_pool().await?;

        let language_rows = sqlx::query(
            r#"
            SELECT l.slug, l.name, COUNT(ce.id)::BIGINT AS documents
            FROM languages l
            JOIN code_examples ce ON ce.language_id = l.id
            GROUP BY l.id
            ORDER BY documents DESC, l.name
            LIMIT 200
            "#,
        )
        .fetch_all(pool)
        .await?;

        let languages = language_rows
            .iter()
            .map(|row| {
                Ok(LanguageItem {
                    slug: row.try_get("slug")?,
                    name: row.try_get("name")?,
                    documents: row.try_get("documents")?,
                })
            })
            .collect::<Result<Vec<_>, sqlx::Error>>()?;

        let category_rows = sqlx::query(
            r#"
            WITH RECURSIVE tree AS (
                SELECT id, slug, name, name AS path
                FROM categories
                WHERE parent_id IS NULL
                UNION ALL
                SELECT c.id, c.slug, c.name, tree.path || '/' || c.name
                FROM categories c
                JOIN tree ON c.parent_id = tree.id
            )
            SELECT tree.slug, tree.name, tree.path,
                   COUNT(cec.code_example_id)::BIGINT AS documents
            FROM tree
            JOIN code_example_categories cec ON cec.category_id = tree.id
            GROUP BY tree.id, tree.slug, tree.name, tree.path
            ORDER BY documents DESC, tree.path
            LIMIT 500
            "#,
        )
        .fetch_all(pool)
        .await?;

        let categories = category_rows
            .iter()
            .map(|row| {
                Ok(CategoryItem {
                    slug: row.try_get("slug")?,
                    name: row.try_get("name")?,
                    path: row.try_get("path")?,
                    documents: row.try_get("documents")?,
                })
            })
            .collect::<Result<Vec<_>, sqlx::Error>>()?;

        Ok::<_, SearchError>(FiltersOutput {
            languages,
            categories,
        })
    })?;

    serde_json::to_string(&output)
        .map_err(|error| SearchError::Internal(error.to_string()))
}
