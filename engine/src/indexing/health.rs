// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! Registre en mémoire de la santé de l'indexation.
//!
//! Il répond à trois besoins :
//! - savoir quels fichiers ont échoué (et pourquoi) pour les retenter ;
//! - savoir quels dossiers ne sont pas inscriptibles par le service (le
//!   rangement physique est alors ignoré et les fichiers sont indexés
//!   sur place) ;
//! - éviter de relire des fichiers inchangés lors d'un nouveau passage
//!   (comparaison taille + date de modification).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

/// Au-delà, un fichier échoué n'est plus retenté automatiquement tant
/// qu'il n'a pas changé (un nouvel événement du disque remet à zéro).
pub const MAX_AUTOMATIC_ATTEMPTS: u32 = 20;

#[derive(Debug, Clone, Serialize)]
pub struct FailedFile {
    pub path: String,
    pub error: String,
    pub attempts: u32,
    pub last_attempt_unix: u64,
}

#[derive(Default)]
struct State {
    failures: BTreeMap<PathBuf, FailedFile>,
    read_only_directories: BTreeSet<PathBuf>,
    indexed: BTreeMap<PathBuf, (u64, i64)>,
    skipped: BTreeMap<PathBuf, String>,
}

static STATE: OnceLock<Mutex<State>> = OnceLock::new();

/// Compteur incrémenté à chaque changement de l'index (document ajouté,
/// modifié ou retiré) : l'API s'en sert pour invalider son cache de recherche.
static GENERATION: AtomicU64 = AtomicU64::new(0);

pub fn generation() -> u64 {
    GENERATION.load(Ordering::SeqCst)
}

fn bump_generation() {
    GENERATION.fetch_add(1, Ordering::SeqCst);
}

/// Charge les signatures (date, taille) mémorisées en base : après un
/// redémarrage, les fichiers inchangés ne sont pas relus (précieux sur un
/// disque dur).
pub fn preload_signatures(entries: impl IntoIterator<Item = (PathBuf, u64, i64)>) -> usize {
    let mut state = state();
    let mut count = 0;
    for (path, mtime, size) in entries {
        state.indexed.insert(path, (mtime, size));
        count += 1;
    }
    count
}

fn state() -> std::sync::MutexGuard<'static, State> {
    STATE
        .get_or_init(|| Mutex::new(State::default()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Signature légère d'un fichier : (secondes depuis epoch, taille).
pub fn signature(path: &Path) -> Option<(u64, i64)> {
    let meta = std::fs::metadata(path).ok()?;
    let modified = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs();
    Some((modified, i64::try_from(meta.len()).ok()?))
}

/// Vrai si le fichier a déjà été indexé avec cette taille et cette date.
pub fn is_unchanged(path: &Path) -> bool {
    let Some(current) = signature(path) else {
        return false;
    };
    state().indexed.get(path) == Some(&current)
}

pub fn record_success(original: &Path, stored: &Path) {
    bump_generation();
    let mut state = state();
    state.failures.remove(original);
    state.skipped.remove(original);
    if let Some(sig) = signature(stored) {
        state.indexed.insert(original.to_path_buf(), sig);
        state.indexed.insert(stored.to_path_buf(), sig);
    }
}

pub fn record_failure(path: &Path, error: &str) {
    let mut state = state();
    let entry = state
        .failures
        .entry(path.to_path_buf())
        .or_insert_with(|| FailedFile {
            path: path.to_string_lossy().into_owned(),
            error: String::new(),
            attempts: 0,
            last_attempt_unix: 0,
        });
    entry.error = error.chars().take(300).collect();
    entry.attempts += 1;
    entry.last_attempt_unix = now();
}

pub fn record_skipped(path: &Path, reason: &str) {
    state().skipped.insert(path.to_path_buf(), reason.to_owned());
}

/// Un nouvel événement du disque remet le compteur d'essais à zéro.
pub fn reset_attempts(path: &Path) {
    if let Some(entry) = state().failures.get_mut(path) {
        entry.attempts = 0;
    }
}

pub fn record_forget(path: &Path) {
    bump_generation();
    let mut state = state();
    state.failures.remove(path);
    state.skipped.remove(path);
    state.indexed.remove(path);
}

/// Note un dossier non inscriptible ; retourne vrai la première fois
/// (pour n'émettre qu'un seul avertissement par dossier).
pub fn note_read_only(directory: &Path) -> bool {
    state().read_only_directories.insert(directory.to_path_buf())
}

pub fn forget_read_only(directory: &Path) {
    state().read_only_directories.remove(directory);
}

/// Fichiers à retenter maintenant.
pub fn retry_candidates() -> Vec<PathBuf> {
    state()
        .failures
        .values()
        .filter(|f| f.attempts < MAX_AUTOMATIC_ATTEMPTS)
        .map(|f| PathBuf::from(&f.path))
        .collect()
}

#[derive(Serialize)]
struct Report {
    failures: Vec<FailedFile>,
    read_only_directories: Vec<String>,
    skipped: Vec<SkippedFile>,
    indexed_in_session: usize,
}

#[derive(Serialize)]
struct SkippedFile {
    path: String,
    reason: String,
}

/// Rapport JSON destiné à l'API (`/api/index/health`).
pub fn report_json() -> String {
    let state = state();
    let report = Report {
        failures: state.failures.values().cloned().collect(),
        read_only_directories: state
            .read_only_directories
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect(),
        skipped: state
            .skipped
            .iter()
            .map(|(p, r)| SkippedFile { path: p.to_string_lossy().into_owned(), reason: r.clone() })
            .collect(),
        indexed_in_session: state.indexed.len(),
    };
    serde_json::to_string(&report).unwrap_or_else(|_| "{}".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failure_then_success_clears_entry() {
        let path = PathBuf::from("/tmp/icc-health-test-a.py");
        record_failure(&path, "Permission denied");
        record_failure(&path, "Permission denied");
        assert!(retry_candidates().contains(&path));
        record_success(&path, &path);
        assert!(!retry_candidates().contains(&path));
    }

    #[test]
    fn attempts_are_capped_until_reset() {
        let path = PathBuf::from("/tmp/icc-health-test-b.py");
        for _ in 0..MAX_AUTOMATIC_ATTEMPTS {
            record_failure(&path, "boom");
        }
        assert!(!retry_candidates().contains(&path));
        reset_attempts(&path);
        assert!(retry_candidates().contains(&path));
        record_forget(&path);
    }

    #[test]
    fn generation_moves_with_the_index_and_signatures_preload() {
        let before = generation();
        let path = PathBuf::from("/tmp/icc-health-test-c.py");
        record_forget(&path);
        assert!(generation() > before);
        assert_eq!(preload_signatures(vec![(path.clone(), 1, 2)]), 1);
        // le fichier n'existe pas : jamais « inchangé »
        assert!(!is_unchanged(&path));
        record_forget(&path);
    }

    #[test]
    fn read_only_warning_is_emitted_once() {
        let dir = PathBuf::from("/tmp/icc-health-ro");
        assert!(note_read_only(&dir));
        assert!(!note_read_only(&dir));
        forget_read_only(&dir);
        assert!(note_read_only(&dir));
        forget_read_only(&dir);
    }
}
