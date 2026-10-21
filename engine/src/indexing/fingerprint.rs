// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

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
#[allow(dead_code)]
pub fn hash_file(path: &Path) -> io::Result<String> {
    let content = fs::read(path)?;
    Ok(hash_content(&content))
}