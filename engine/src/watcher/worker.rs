use std::path::PathBuf;
use std::time::Duration;

use crate::database::documents::remove_document_by_path;
use crate::indexing::document::{index_file_tracked, is_supported_file};
use crate::indexing::health;

use super::events::FileAction;

/// Traite une action reçue du watcher.
pub async fn process_action(
    action: FileAction,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match action {
        FileAction::Upsert(path) => {
            if path.is_file() && is_supported_file(&path) {
                health::reset_attempts(&path);
                match index_file_tracked(&path).await {
                    Ok(true) => {
                        println!(
                            "CodeCenter : document indexé ou actualisé : {}",
                            path.display()
                        );
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
                schedule_removal(path);
            }
        }

        FileAction::Remove(path) => {
            schedule_removal(path);
        }
    }

    Ok(())
}

/// Diffère la suppression pour laisser le temps à l'événement de
/// création ou de déplacement d'actualiser le chemin en base.
fn schedule_removal(path: PathBuf) {
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(1200)).await;

        if path.exists() {
            return;
        }

        if let Err(error) = remove_path(&path).await {
            eprintln!(
                "CodeCenter : impossible de supprimer l'entrée {} : {}",
                path.display(),
                error
            );
        }
    });
}

/// Supprime l'entrée PostgreSQL associée à un ancien chemin.
async fn remove_path(
    path: &PathBuf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    health::record_forget(path);
    let storage_path = path.to_string_lossy().into_owned();

    if remove_document_by_path(&storage_path).await? {
        println!("CodeCenter : document supprimé : {storage_path}");
    }

    Ok(())
}
