use sha2::{Digest, Sha256};
use std::fs;
use std::io;
use std::path::Path;

/// Calcule le SHA-256 du contenu fourni.
pub fn hash_content(content: &[u8]) -> String {
    let digest = Sha256::digest(content);
    format!("{digest:x}")
}

/// Calcule le SHA-256 d'un fichier.
pub fn hash_file(path: &Path) -> io::Result<String> {
    let content = fs::read(path)?;
    Ok(hash_content(&content))
}