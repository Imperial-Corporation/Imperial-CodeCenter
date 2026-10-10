use std::path::Path;

use crate::indexing::document::{index_file, is_supported_file};

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

    let mut pending = vec![root];

    while let Some(directory) = pending.pop() {
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
                if !should_skip_directory(&path) {
                    pending.push(path);
                }
                continue;
            }

            if !file_type.is_file() || !is_supported_file(&path) {
                continue;
            }

            if let Err(error) = index_file(&path).await {
                eprintln!(
                    "CodeCenter : échec d'indexation de {} : {}",
                    path.display(),
                    error
                );
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
    )
}