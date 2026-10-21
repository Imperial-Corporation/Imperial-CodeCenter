// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! État du passage d'indexation en cours (lu par l'API pour l'afficher en direct)
//! et demande de passage immédiat.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tokio::sync::Notify;

#[derive(Debug, Clone, Default, Serialize)]
pub struct ScanState {
    /// Un passage est en cours.
    pub running: bool,
    /// `initial`, `periodic` ou `manual`.
    pub kind: String,
    pub started_unix: u64,
    pub finished_unix: u64,
    /// Fichiers pris en charge rencontrés pendant ce passage.
    pub seen: u64,
    /// Fichiers (ré)indexés.
    pub indexed: u64,
    /// Fichiers laissés tels quels (inchangés).
    pub unchanged: u64,
    pub failed: u64,
    pub excluded: u64,
    /// Dossier en cours de lecture.
    pub current: String,
    /// Nombre de fichiers vus au passage précédent (sert d'estimation de total).
    pub previous_total: u64,
    pub last_duration_secs: u64,
    pub watcher_running: bool,
    pub pause_ms: u64,
}

static STATE: OnceLock<Mutex<ScanState>> = OnceLock::new();
static NOTIFY: OnceLock<Notify> = OnceLock::new();
static WATCHING: AtomicBool = AtomicBool::new(false);

fn state() -> MutexGuard<'static, ScanState> {
    STATE
        .get_or_init(|| Mutex::new(ScanState::default()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn rescan_notify() -> &'static Notify {
    NOTIFY.get_or_init(Notify::new)
}

/// Demande un passage immédiat (le surveillant le lance dès qu'il est libre).
pub fn request_rescan() {
    rescan_notify().notify_one();
}

pub fn set_watching(value: bool) {
    WATCHING.store(value, Ordering::SeqCst);
}

pub fn begin(kind: &str) {
    let mut s = state();
    s.previous_total = if s.seen > 0 { s.seen } else { s.previous_total };
    s.running = true;
    s.kind = kind.to_owned();
    s.started_unix = now();
    s.seen = 0;
    s.indexed = 0;
    s.unchanged = 0;
    s.failed = 0;
    s.excluded = 0;
    s.current.clear();
}

pub fn finish() {
    let mut s = state();
    let ended = now();
    s.running = false;
    s.finished_unix = ended;
    s.last_duration_secs = ended.saturating_sub(s.started_unix);
    s.current.clear();
}

pub fn enter_directory(path: &std::path::Path) {
    state().current = path.to_string_lossy().chars().take(300).collect();
}

pub fn note_seen() {
    state().seen += 1;
}

pub fn note_indexed() {
    state().indexed += 1;
}

pub fn note_unchanged() {
    state().unchanged += 1;
}

pub fn note_failed() {
    state().failed += 1;
}

pub fn note_excluded() {
    state().excluded += 1;
}

/// Pause (ms) entre deux fichiers indexés : ménage un disque lent ou un petit CPU.
pub fn pause_ms() -> u64 {
    std::env::var("CODECENTER_INDEX_PAUSE_MS")
        .ok()
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(0)
        .min(5_000)
}

pub fn snapshot_json() -> String {
    let mut s = state().clone();
    s.watcher_running = WATCHING.load(Ordering::SeqCst);
    s.pause_ms = pause_ms();
    serde_json::json!({
        "scan": s,
        "generation": crate::indexing::health::generation(),
    })
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_lifecycle_is_tracked() {
        begin("manual");
        note_seen();
        note_seen();
        note_indexed();
        note_unchanged();
        let json: serde_json::Value = serde_json::from_str(&snapshot_json()).unwrap();
        assert_eq!(json["scan"]["running"], true);
        assert_eq!(json["scan"]["seen"], 2);
        assert_eq!(json["scan"]["kind"], "manual");
        finish();
        begin("periodic");
        let json: serde_json::Value = serde_json::from_str(&snapshot_json()).unwrap();
        assert_eq!(json["scan"]["previous_total"], 2);
        finish();
        let json: serde_json::Value = serde_json::from_str(&snapshot_json()).unwrap();
        assert_eq!(json["scan"]["running"], false);
    }

    #[test]
    fn pause_is_bounded() {
        // SAFETY : variable propre à ce test.
        unsafe { std::env::set_var("CODECENTER_INDEX_PAUSE_MS", "999999") };
        assert_eq!(pause_ms(), 5_000);
        unsafe { std::env::remove_var("CODECENTER_INDEX_PAUSE_MS") };
        assert_eq!(pause_ms(), 0);
    }
}
