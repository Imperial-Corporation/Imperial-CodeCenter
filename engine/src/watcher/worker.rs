use std::path::PathBuf;

use crate::database::documents::remove_document_by_path;
use crate::indexing::document::{index_file, is_supported_file};

use super::events::FileAction;

/// Traite une action reçue du watcher.
pub async fn process_action(
    action: FileAction,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match action {
        FileAction::Upsert(path) => {
            // Un événement peut arriver avant que le fichier soit entièrement écrit.
            // S'il existe et qu'il est pris en charge, on tente de l'indexer.
            if path.is_file() && is_supported_file(&path) {
                match index_file(&path).await {
                    Ok(true) => {
                        println!("CodeCenter : document actualisé : {}", path.display());
                    }
                    Ok(false) => {}
                    Err(error) => {
                        eprintln!(
                            "CodeCenter : impossible d'indexer {} : {}",
                            path.display(),
                            error
                        );
                        return Err(error);
                    }
                }
            } else if !path.exists() {
                // Le fichier a pu être supprimé avant le traitement de l'événement.
                remove_path(&path).await?;
            }
        }

        FileAction::Remove(path) => {
            remove_path(&path).await?;
        }
    }

    Ok(())
}

/// Supprime l'entrée PostgreSQL associée à un chemin.
async fn remove_path(
    path: &PathBuf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Un chemin supprimé ne peut plus être canonisé. Le chemin transmis
    // doit donc correspondre à celui enregistré avant la suppression.
    let storage_path = path.to_string_lossy().into_owned();

    if remove_document_by_path(&storage_path).await? {
        println!("CodeCenter : document supprimé : {storage_path}");
    }

    Ok(())
}