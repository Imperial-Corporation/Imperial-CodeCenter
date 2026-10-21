// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! Exclusions configurables : fichiers et dossiers jamais indexés.
//!
//! Sources (cumulées) :
//! - `CODECENTER_EXCLUDE` : motifs séparés par des virgules ou des retours à la ligne ;
//! - `CODECENTER_EXCLUDE_FILE` : un motif par ligne (`#` = commentaire), relu au plus
//!   toutes les 5 secondes, donc modifiable sans redémarrer.
//!
//! Syntaxe (insensible à la casse, `*` = n'importe quels caractères, `?` = un caractère) :
//! - sans `/` : le motif est comparé au nom de chaque dossier ou fichier du chemin
//!   (relatif à la racine surveillée) — `*.min.js`, `node_modules`, `tmp*` ;
//! - avec `/` : le motif désigne une suite de dossiers n'importe où sous la racine —
//!   `vendor/legacy`, `docs/*/generated`.

use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

const REFRESH_EVERY: Duration = Duration::from_secs(5);

struct Cache {
    loaded: Option<Instant>,
    patterns: Arc<Vec<String>>,
}

static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();

fn cache() -> &'static Mutex<Cache> {
    CACHE.get_or_init(|| {
        Mutex::new(Cache {
            loaded: None,
            patterns: Arc::new(Vec::new()),
        })
    })
}

/// Lit les motifs depuis l'environnement et le fichier.
pub fn load_patterns() -> Vec<String> {
    let mut patterns: Vec<String> = Vec::new();

    if let Ok(value) = std::env::var("CODECENTER_EXCLUDE") {
        patterns.extend(
            value
                .split(|c| c == ',' || c == '\n')
                .map(|p| p.trim().to_owned()),
        );
    }

    if let Some(file) = std::env::var_os("CODECENTER_EXCLUDE_FILE") {
        if let Ok(text) = std::fs::read_to_string(file) {
            patterns.extend(text.lines().map(|p| p.trim().to_owned()));
        }
    }

    let mut cleaned: Vec<String> = patterns
        .into_iter()
        .filter(|p| !p.is_empty() && !p.starts_with('#'))
        .map(|p| p.replace('\\', "/").to_lowercase())
        .collect();
    cleaned.sort();
    cleaned.dedup();
    cleaned
}

/// Force la relecture (début de chaque passage).
pub fn refresh() -> Arc<Vec<String>> {
    let patterns = Arc::new(load_patterns());
    let mut guard = cache().lock().unwrap_or_else(|e| e.into_inner());
    guard.loaded = Some(Instant::now());
    guard.patterns = patterns.clone();
    patterns
}

fn patterns() -> Arc<Vec<String>> {
    {
        let guard = cache().lock().unwrap_or_else(|e| e.into_inner());
        if let Some(loaded) = guard.loaded {
            if loaded.elapsed() < REFRESH_EVERY {
                return guard.patterns.clone();
            }
        }
    }
    refresh()
}

/// Empreinte des motifs courants (pour savoir si la liste a changé).
pub fn signature() -> String {
    patterns().join("\n")
}

/// Correspondance glob insensible à la casse (`*` et `?`).
pub fn glob_match(pattern: &str, text: &str) -> bool {
    let pattern: Vec<char> = pattern.chars().collect();
    let text: Vec<char> = text.chars().collect();
    let (mut p, mut t) = (0usize, 0usize);
    let mut star: Option<usize> = None;
    let mut mark = 0usize;

    while t < text.len() {
        if p < pattern.len()
            && (pattern[p] == '?' || pattern[p].to_lowercase().eq(text[t].to_lowercase()))
        {
            p += 1;
            t += 1;
        } else if p < pattern.len() && pattern[p] == '*' {
            star = Some(p);
            mark = t;
            p += 1;
        } else if let Some(s) = star {
            p = s + 1;
            mark += 1;
            t = mark;
        } else {
            return false;
        }
    }

    while p < pattern.len() && pattern[p] == '*' {
        p += 1;
    }
    p == pattern.len()
}

/// Partie du chemin située sous la racine surveillée (ou le chemin entier).
fn relative_to_roots(path: &Path) -> String {
    let mut text = path.to_string_lossy().replace('\\', "/");
    if let Some(configured) = std::env::var_os("CODECENTER_WATCH_PATHS") {
        let mut best: Option<String> = None;
        for root in std::env::split_paths(&configured) {
            let candidates = [Some(root.clone()), root.canonicalize().ok()];
            for candidate in candidates.into_iter().flatten() {
                if let Ok(rest) = path.strip_prefix(&candidate) {
                    let rest = rest.to_string_lossy().replace('\\', "/");
                    if best.as_ref().map_or(true, |b| rest.len() < b.len()) {
                        best = Some(rest);
                    }
                }
            }
        }
        if let Some(rest) = best {
            text = rest;
        }
    }
    text
}

/// Vrai si ce chemin correspond à un motif d'exclusion.
pub fn is_excluded(path: &Path) -> bool {
    let patterns = patterns();
    if patterns.is_empty() {
        return false;
    }
    is_excluded_with(&patterns, &relative_to_roots(path))
}

/// Logique pure (testable) : `relative` est un chemin à base de `/`.
pub fn is_excluded_with(patterns: &[String], relative: &str) -> bool {
    let relative = relative.trim_matches('/').to_lowercase();
    if relative.is_empty() {
        return false;
    }
    let wrapped = format!("/{relative}/");

    patterns.iter().any(|pattern| {
        let pattern = pattern.trim_matches('/');
        if pattern.is_empty() {
            return false;
        }
        if pattern.contains('/') {
            glob_match(&format!("*/{pattern}/*"), &wrapped)
        } else {
            relative.split('/').any(|name| glob_match(pattern, name))
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn glob_basics() {
        assert!(glob_match("*.min.js", "app.MIN.js"));
        assert!(glob_match("tmp*", "tmp-1"));
        assert!(glob_match("a?c", "abc"));
        assert!(glob_match("*", ""));
        assert!(!glob_match("a?c", "ac"));
        assert!(!glob_match("*.js", "app.jsx"));
        assert!(glob_match("*a*b*", "xxaYYbzz"));
        assert!(!glob_match("*a*b*", "xxbYYazz"));
    }

    #[test]
    fn name_patterns_apply_to_any_component() {
        let p = vec!["node_modules".to_owned(), "*.min.js".to_owned()];
        assert!(is_excluded_with(&p, "web/node_modules/x/index.js"));
        assert!(is_excluded_with(&p, "web/app.min.js"));
        assert!(!is_excluded_with(&p, "web/app.js"));
        assert!(!is_excluded_with(&p, ""));
    }

    #[test]
    fn path_patterns_match_component_sequences() {
        let p = vec!["vendor/legacy".to_owned(), "docs/*/generated".to_owned()];
        assert!(is_excluded_with(&p, "a/vendor/legacy/x.py"));
        assert!(is_excluded_with(&p, "vendor/legacy"));
        assert!(!is_excluded_with(&p, "vendor/legacyx/x.py"));
        assert!(is_excluded_with(&p, "docs/api/generated/x.py"));
        assert!(!is_excluded_with(&p, "docs/api/handwritten/x.py"));
    }

    #[test]
    fn patterns_load_from_environment_and_ignore_comments() {
        // SAFETY : variables propres à ce test (aucun autre test ne les lit).
        unsafe {
            std::env::set_var("CODECENTER_EXCLUDE", "Build_Out, *.tmp\n# note\n");
            std::env::remove_var("CODECENTER_EXCLUDE_FILE");
        }
        let loaded = load_patterns();
        assert_eq!(loaded, vec!["*.tmp".to_owned(), "build_out".to_owned()]);
        unsafe { std::env::remove_var("CODECENTER_EXCLUDE") };
    }
}
