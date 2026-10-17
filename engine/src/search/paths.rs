// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

//! Résolution sécurisée des chemins de fichiers indexés.
//!
//! Un chemin lu en base n'est jamais ouvert tel quel : il doit, une fois
//! canonicalisé (liens symboliques résolus), rester dans l'une des racines
//! de `CODECENTER_WATCH_PATHS`.

use std::fmt;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum PathError {
    /// `CODECENTER_WATCH_PATHS` absente ou sans dossier valide.
    Config(String),
    /// Le fichier est hors des racines autorisées.
    Outside,
    /// Le chemin n'est pas un fichier régulier lisible.
    NotFile(String),
}

impl fmt::Display for PathError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PathError::Config(message) => write!(f, "{message}"),
            PathError::Outside => {
                write!(f, "Chemin en dehors des dossiers autorisés.")
            }
            PathError::NotFile(message) => write!(f, "{message}"),
        }
    }
}

/// Racines autorisées, canonicalisées.
pub fn watch_roots() -> Result<Vec<PathBuf>, PathError> {
    let configured = std::env::var_os("CODECENTER_WATCH_PATHS").ok_or_else(|| {
        PathError::Config("CODECENTER_WATCH_PATHS est absente.".into())
    })?;

    let roots: Vec<PathBuf> = std::env::split_paths(&configured)
        .filter_map(|root| root.canonicalize().ok())
        .filter(|root| root.is_dir())
        .collect();

    if roots.is_empty() {
        return Err(PathError::Config(
            "Aucun dossier valide dans CODECENTER_WATCH_PATHS.".into(),
        ));
    }

    Ok(roots)
}

/// Vérifie qu'un chemin appartient aux racines et retourne
/// (chemin canonique, racine correspondante).
pub fn resolve_in_roots(
    storage_path: &str,
    roots: &[PathBuf],
) -> Result<(PathBuf, PathBuf), PathError> {
    let canonical = Path::new(storage_path)
        .canonicalize()
        .map_err(|_| PathError::NotFile("Fichier introuvable sur le disque.".into()))?;

    // Si les racines se chevauchent, la plus spécifique est retenue.
    let root = roots
        .iter()
        .filter(|root| canonical.starts_with(root))
        .max_by_key(|root| root.components().count())
        .ok_or(PathError::Outside)?;

    if !canonical.is_file() {
        return Err(PathError::NotFile("Le chemin n'est pas un fichier.".into()));
    }

    Ok((canonical, root.clone()))
}

/// Chemin affichable : relatif à la racine, séparateurs `/`.
/// Évite d'exposer l'organisation du serveur.
pub fn relative_display(path: &Path, root: &Path) -> String {
    let relative = path.strip_prefix(root).unwrap_or(path);
    let label = root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("");

    let joined = relative
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/");

    if label.is_empty() {
        joined
    } else {
        format!("{label}/{joined}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "icc-test-{name}-{}-{}",
            std::process::id(),
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir.canonicalize().unwrap()
    }

    #[test]
    fn accepte_un_fichier_dans_la_racine() {
        let root = temp_dir("ok");
        let file = root.join("a.rs");
        fs::write(&file, "fn a() {}").unwrap();

        let (path, found_root) =
            resolve_in_roots(file.to_str().unwrap(), &[root.clone()]).unwrap();

        assert_eq!(path, file);
        assert_eq!(found_root, root);
    }

    #[test]
    fn refuse_un_fichier_hors_racine() {
        let root = temp_dir("root");
        let other = temp_dir("other");
        let file = other.join("secret.txt");
        fs::write(&file, "x").unwrap();

        let result = resolve_in_roots(file.to_str().unwrap(), &[root]);
        assert!(matches!(result, Err(PathError::Outside)));
    }

    #[test]
    fn refuse_la_traversee_par_points_points() {
        let root = temp_dir("trav");
        let other = temp_dir("trav-other");
        fs::write(other.join("s.txt"), "x").unwrap();

        let sneaky = format!(
            "{}/../{}/s.txt",
            root.display(),
            other.file_name().unwrap().to_string_lossy()
        );

        let result = resolve_in_roots(&sneaky, &[root]);
        assert!(matches!(result, Err(PathError::Outside)));
    }

    #[cfg(unix)]
    #[test]
    fn refuse_un_lien_symbolique_sortant() {
        let root = temp_dir("sym");
        let other = temp_dir("sym-other");
        let target = other.join("s.txt");
        fs::write(&target, "x").unwrap();
        let link = root.join("lien.txt");
        std::os::unix::fs::symlink(&target, &link).unwrap();

        let result = resolve_in_roots(link.to_str().unwrap(), &[root]);
        assert!(matches!(result, Err(PathError::Outside)));
    }

    #[test]
    fn refuse_un_dossier() {
        let root = temp_dir("dir");
        let sub = root.join("sub");
        fs::create_dir_all(&sub).unwrap();

        let result = resolve_in_roots(sub.to_str().unwrap(), &[root]);
        assert!(matches!(result, Err(PathError::NotFile(_))));
    }

    #[test]
    fn chemin_affichable_est_relatif() {
        let root = PathBuf::from("/srv/codes");
        let file = PathBuf::from("/srv/codes/rust/a/b.rs");
        assert_eq!(relative_display(&file, &root), "codes/rust/a/b.rs");
    }
}
