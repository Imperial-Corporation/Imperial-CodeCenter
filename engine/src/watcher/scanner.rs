// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

use std::path::Path;

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use crate::indexing::document::{index_file_tracked, is_supported_file};
use crate::indexing::{exclusions, health};

use super::status;

static SIGNATURES_LOADED: AtomicBool = AtomicBool::new(false);

/// Charge une fois les signatures mémorisées en base (voir `health::preload_signatures`).
async fn preload_signatures_once() {
    if SIGNATURES_LOADED.swap(true, Ordering::SeqCst) {
        return;
    }
    let entries = crate::database::content::load_signatures().await;
    let count = health::preload_signatures(entries);
    if count > 0 {
        println!("CodeCenter : {count} signature(s) de fichier rechargée(s) depuis la base (pas de relecture).");
    }
}

/// Parcourt récursivement un dossier et indexe les fichiers pris en charge.
pub async fn scan_directory(
    root: &Path,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let root = root.canonicalize()?;

    if !root.is_dir() {
        return Err(format!(
            "Le chemin surveillé n'est pas un dossier : {}",
            root.display()
        )
        .into());
    }

    preload_signatures_once().await;
    let pause = status::pause_ms();
    let mut pending = vec![root];

    while let Some(directory) = pending.pop() {
        status::enter_directory(&directory);
        let entries = match std::fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) => {
                eprintln!(
                    "CodeCenter : impossible de lire {} : {}",
                    directory.display(),
                    error
                );
                continue;
            }
        };

        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) => {
                    eprintln!("CodeCenter : entrée inaccessible : {error}");
                    continue;
                }
            };

            let path = entry.path();

            // Ne suit pas les liens symboliques : évite les boucles et
            // les sorties accidentelles du dossier surveillé.
            let file_type = match entry.file_type() {
                Ok(file_type) => file_type,
                Err(error) => {
                    eprintln!(
                        "CodeCenter : type inaccessible pour {} : {}",
                        path.display(),
                        error
                    );
                    continue;
                }
            };

            if file_type.is_symlink() {
                continue;
            }

            if file_type.is_dir() {
                if should_skip_directory(&path) {
                    continue;
                }
                if exclusions::is_excluded(&path) {
                    status::note_excluded();
                    continue;
                }
                pending.push(path);
                continue;
            }

            if !file_type.is_file() || !is_supported_file(&path) {
                continue;
            }

            if exclusions::is_excluded(&path) {
                status::note_excluded();
                continue;
            }

            status::note_seen();

            // Passage de rattrapage : on ne relit pas ce qui n'a pas changé.
            if health::is_unchanged(&path) {
                status::note_unchanged();
                continue;
            }

            match index_file_tracked(&path).await {
                Ok(_) => status::note_indexed(),
                Err(error) => {
                    status::note_failed();
                    eprintln!(
                        "CodeCenter : échec d'indexation de {} : {}",
                        path.display(),
                        error
                    );
                }
            }

            // Ménage le disque et le processeur (profil léger).
            if pause > 0 {
                tokio::time::sleep(Duration::from_millis(pause)).await;
            }
        }
    }

    Ok(())
}

/// Ignore les répertoires techniques et les dépôts imbriqués.
fn should_skip_directory(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };

    matches!(
        name,
        ".git"
            | ".hg"
            | ".svn"
            | "node_modules"
            | "target"
            | "vendor"
            | "__pycache__"
            | ".venv"
            | "venv"
            | "build"
            | "dist"
            | ".crawler"
            | "_depot"
    )
}