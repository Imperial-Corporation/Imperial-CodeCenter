use std::fs;
use std::io;
use std::path::Path;

use crate::database::documents::upsert_document;
use crate::indexing::fingerprint::hash_content;

/// Traite un fichier et actualise son entrée dans PostgreSQL.
///
/// Retourne `true` si la base de données a été modifiée.
pub async fn index_file(path: &Path) -> Result<bool, Box<dyn std::error::Error + Send + Sync>> {
    let metadata = fs::metadata(path)?;

    if !metadata.is_file() {
        return Ok(false);
    }

    let content = fs::read(path)?;
    let content_sha256 = hash_content(&content);
    let file_size = i64::try_from(content.len())?;

    let storage_path = path
        .canonicalize()?
        .to_string_lossy()
        .into_owned();

    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown");

    let changed = upsert_document(
        &storage_path,
        filename,
        &content_sha256,
        file_size,
    )
    .await?;

    Ok(changed)
}

/// Indique si un fichier peut être pris en charge par l'indexeur.
pub fn is_supported_file(path: &Path) -> bool {
    let Some(extension) = path.extension().and_then(|ext| ext.to_str()) else {
        return false;
    };

    matches!(
        extension.to_ascii_lowercase().as_str(),
        "c" | "h" | "cpp" | "hpp" | "rs" | "py" | "js" | "ts"
            | "jsx" | "tsx" | "java" | "cs" | "go" | "rb" | "php"
            | "swift" | "kt" | "kts" | "scala" | "sh" | "bash"
            | "html" | "css" | "sql" | "lua" | "zig" | "toml"
            | "json" | "yaml" | "yml"
    )
}