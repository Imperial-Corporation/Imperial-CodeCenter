use std::path::PathBuf;

use notify_debouncer_full::notify::{Event, EventKind};

use crate::indexing::document::is_supported_file;

#[derive(Debug, Clone)]
pub enum FileAction {
    Upsert(PathBuf),
    Remove(PathBuf),
}

pub fn event_to_actions(event: Event) -> Vec<FileAction> {
    let Event { kind, paths, .. } = event;

    match kind {
        EventKind::Create(_) | EventKind::Modify(_) => paths
            .into_iter()
            .filter(|path| is_supported_file(path))
            .map(FileAction::Upsert)
            .collect(),

        EventKind::Remove(_) => paths
            .into_iter()
            .filter(|path| is_supported_file(path))
            .map(FileAction::Remove)
            .collect(),

        _ => Vec::new(),
    }
}