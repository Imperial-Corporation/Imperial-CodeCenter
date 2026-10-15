use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use crate::database::documents::upsert_document;
use crate::indexing::fingerprint::hash_content;
use crate::indexing::health;

const LANGUAGE_MARKER: &str = ".codecenter-language-dir";

struct Language {
    name: &'static str,
    slug: &'static str,
}

fn language_for_extension(extension: &str) -> Option<Language> {
    let language = match extension.to_ascii_lowercase().as_str() {
        "c" | "h" => ("C", "c"),
        "cc" | "cpp" | "cxx" | "hh" | "hpp" | "hxx" => ("C++", "cpp"),
        "rs" => ("Rust", "rust"),
        "py" | "pyi" => ("Python", "python"),
        "js" | "jsx" | "mjs" | "cjs" => ("JavaScript", "javascript"),
        "ts" | "tsx" | "mts" | "cts" => ("TypeScript", "typescript"),
        "java" => ("Java", "java"),
        "cs" => ("C#", "csharp"),
        "go" => ("Go", "go"),
        "rb" => ("Ruby", "ruby"),
        "php" => ("PHP", "php"),
        "swift" => ("Swift", "swift"),
        "kt" | "kts" => ("Kotlin", "kotlin"),
        "scala" => ("Scala", "scala"),
        "sh" | "bash" => ("Shell", "shell"),
        "html" | "htm" => ("HTML", "html"),
        "css" => ("CSS", "css"),
        "sql" => ("SQL", "sql"),
        "lua" => ("Lua", "lua"),
        "zig" => ("Zig", "zig"),
        "toml" => ("TOML", "toml"),
        "json" => ("JSON", "json"),
        "yaml" | "yml" => ("YAML", "yaml"),
        _ => return None,
    };

    Some(Language {
        name: language.0,
        slug: language.1,
    })
}

/// Taille maximale d'un fichier indexé (octets). Au-delà, il est ignoré
/// et signalé dans le rapport de santé. Modifiable par
/// `CODECENTER_MAX_FILE_BYTES`.
fn max_file_bytes() -> u64 {
    std::env::var("CODECENTER_MAX_FILE_BYTES")
        .ok()
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(2_000_000)
}

/// Le rangement physique (déplacement dans `<catégorie>/<langage>/`) est
/// activé par défaut. `CODECENTER_ORGANIZE=off` l'interdit : les fichiers
/// sont alors indexés exactement là où ils se trouvent.
fn organizing_enabled() -> bool {
    !matches!(
        std::env::var("CODECENTER_ORGANIZE")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "off" | "0" | "false" | "no" | "non"
    )
}

/// Vérifie qu'on peut réellement écrire dans un dossier avec les droits
/// du processus (les bits de permission seuls ne suffisent pas : ACL,
/// groupes, systemd `ReadWritePaths`, montages en lecture seule…).
fn directory_is_writable(directory: &Path) -> bool {
    let probe = directory.join(format!(".codecenter-probe-{}", std::process::id()));
    match fs::OpenOptions::new().write(true).create_new(true).open(&probe) {
        Ok(_) => {
            let _ = fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// Range le fichier si c'est possible ; sinon le laisse en place.
///
/// Indexer un fichier ne doit jamais dépendre du droit de le déplacer :
/// en cas de dossier non inscriptible ou d'erreur d'E/S pendant le
/// rangement, on conserve le chemin d'origine (avertissement unique par
/// dossier) au lieu de perdre le fichier pour la recherche.
fn organize_or_keep(
    path: &Path,
    language: &Language,
    content_sha256: &str,
) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
    let in_place = || -> Result<PathBuf, Box<dyn Error + Send + Sync>> { Ok(path.canonicalize()?) };

    if !organizing_enabled() {
        return in_place();
    }

    let parent = path.parent().ok_or("Dossier parent absent")?;
    let marker = parent.join(LANGUAGE_MARKER);

    // Déjà au bon endroit : aucune écriture nécessaire.
    if marker.is_file() && fs::read_to_string(&marker).map(|m| m.trim() == language.slug).unwrap_or(false) {
        return in_place();
    }

    if !directory_is_writable(parent) {
        if health::note_read_only(parent) {
            eprintln!(
                "CodeCenter : {} n'est pas inscriptible par le service ; les fichiers y sont indexés sans être rangés \
                 (voir docs/permissions.md).",
                parent.display()
            );
        }
        return in_place();
    }
    health::forget_read_only(parent);

    match organize_file(path, language, content_sha256) {
        Ok(organized) => Ok(organized),
        Err(error) => {
            eprintln!(
                "CodeCenter : rangement impossible pour {} ({error}) ; indexation sur place.",
                path.display()
            );
            in_place()
        }
    }
}

/// Déplace le fichier dans le sous-dossier du langage.
///
/// Un dossier de langage est reconnu par son marqueur, pas seulement
/// par son nom : une catégorie nommée "python" reste donc une catégorie.
fn organize_file(
    path: &Path,
    language: &Language,
    content_sha256: &str,
) -> Result<PathBuf, Box<dyn Error + Send + Sync>> {
    let parent = path.parent().ok_or("Dossier parent absent")?;
    let marker = parent.join(LANGUAGE_MARKER);

    if marker.is_file()
        && fs::read_to_string(&marker)?.trim() == language.slug
    {
        return Ok(path.canonicalize()?);
    }

    // Si le fichier est déjà dans un dossier de langage, revenir à
    // sa catégorie parente avant de choisir le bon langage.
    let category_parent = if marker.is_file() {
        parent.parent().ok_or("Catégorie parente absente")?
    } else {
        parent
    };

    let language_dir = category_parent.join(language.slug);
    fs::create_dir_all(&language_dir)?;

    let language_marker = language_dir.join(LANGUAGE_MARKER);

    if !language_marker.exists() {
        fs::write(&language_marker, language.slug)?;
    }

    let filename = path.file_name().ok_or("Nom de fichier absent")?;
    let mut destination = language_dir.join(filename);

    // Ne jamais écraser un fichier existant.
    if destination.exists() && destination != path {
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("code");
        let extension = path.extension().and_then(|s| s.to_str()).unwrap_or("");
        let suffix = &content_sha256[..8.min(content_sha256.len())];

        let renamed = if extension.is_empty() {
            format!("{stem}-{suffix}")
        } else {
            format!("{stem}-{suffix}.{extension}")
        };

        destination = language_dir.join(renamed);

        if destination.exists() && destination != path {
            return Err(format!(
                "Destination déjà occupée : {}",
                destination.display()
            ).into());
        }
    }

    if path != destination {
        fs::rename(path, &destination)?;
    }

    Ok(destination.canonicalize()?)
}

/// Calcule les catégories depuis la racine surveillée jusqu'au fichier.
/// Les dossiers marqués comme dossiers de langage ne deviennent pas des tags.
fn categories_for_path(
    path: &Path,
    language_slug: &str,
) -> Result<Vec<String>, Box<dyn Error + Send + Sync>> {
    let configured = std::env::var_os("CODECENTER_WATCH_PATHS")
        .ok_or("CODECENTER_WATCH_PATHS est absente")?;

    let canonical_file = path.canonicalize()?;
    let mut roots: Vec<PathBuf> = std::env::split_paths(&configured)
        .filter_map(|root| root.canonicalize().ok())
        .filter(|root| canonical_file.starts_with(root))
        .collect();

    // Si les racines se chevauchent, utiliser la plus spécifique.
    roots.sort_by_key(|root| std::cmp::Reverse(root.components().count()));

    let root = roots.first().ok_or("Aucune racine ne contient ce fichier")?;
    let parent = canonical_file.parent().ok_or("Dossier parent absent")?;
    let relative = parent.strip_prefix(root)?;

    let mut current = root.to_path_buf();
    let mut categories = Vec::new();

    let component_count = relative.components().count();

    for (index, component) in relative.components().enumerate() {
        current.push(component);

        // Dossier non rangé mais nommé comme le langage (`aes/python/`) :
        // c'est un dossier de langage de fait, pas une catégorie.
        let is_last = index + 1 == component_count;
        if is_last
            && component
                .as_os_str()
                .to_str()
                .is_some_and(|name| name.eq_ignore_ascii_case(language_slug))
        {
            continue;
        }

        if current.join(LANGUAGE_MARKER).is_file() {
            continue;
        }

        if let Some(name) = component.as_os_str().to_str() {
            if !name.is_empty() {
                categories.push(name.to_owned());
            }
        }
    }

    Ok(categories)
}

/// Dossiers techniques jamais indexés : la zone de dépôt (`_depot`, vidée
/// par l'API après rangement) et l'état du crawler (`.crawler`).
fn is_ignored_path(path: &Path) -> bool {
    path.ancestors().skip(1).any(|ancestor| {
        ancestor
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name == "_depot" || name == ".crawler")
    })
}

/// Indexe un fichier, le range par langage et synchronise PostgreSQL.
pub async fn index_file(
    path: &Path,
) -> Result<bool, Box<dyn Error + Send + Sync>> {
    if !path.is_file() || is_ignored_path(path) {
        return Ok(false);
    }

    let extension = path
        .extension()
        .and_then(|ext| ext.to_str())
        .ok_or("Extension absente")?
        .to_ascii_lowercase();

    let language = language_for_extension(&extension)
        .ok_or_else(|| format!("Langage non pris en charge : {extension}"))?;

    let size = fs::metadata(path)?.len();
    if size > max_file_bytes() {
        health::record_skipped(path, &format!("fichier trop volumineux ({size} octets)"));
        return Ok(false);
    }

    let original_content = fs::read(path)?;
    let content_sha256 = hash_content(&original_content);

    let organized_path = organize_or_keep(path, &language, &content_sha256)?;

    let content = fs::read(&organized_path)?;
    let content_sha256 = hash_content(&content);
    let file_size = i64::try_from(content.len())?;

    let storage_path = organized_path.to_string_lossy().into_owned();
    let filename = organized_path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown");

    let categories = categories_for_path(&organized_path, language.slug)?;

    let result = upsert_document(
        &storage_path,
        filename,
        &content_sha256,
        file_size,
        language.name,
        language.slug,
        &extension,
        &categories,
    )
    .await
    .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;

    health::record_success(path, &organized_path);
    Ok(result)
}

/// Indexe un fichier en enregistrant l'échec éventuel dans le registre
/// de santé (consulté par l'API et utilisé pour les nouvelles tentatives).
pub async fn index_file_tracked(
    path: &Path,
) -> Result<bool, Box<dyn Error + Send + Sync>> {
    match index_file(path).await {
        Ok(result) => Ok(result),
        Err(error) => {
            health::record_failure(path, &error.to_string());
            Err(error)
        }
    }
}

/// Indique si l'extension est prise en charge.
pub fn is_supported_file(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .and_then(language_for_extension)
        .is_some()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("icc-doc-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn running_as_root() -> bool {
        fs::read_to_string("/proc/self/status")
            .map(|s| s.lines().any(|l| l.starts_with("Uid:") && l.split_whitespace().nth(1) == Some("0")))
            .unwrap_or(false)
    }

    fn python() -> Language {
        language_for_extension("py").unwrap()
    }

    #[test]
    fn writable_directory_gets_organized() {
        let root = temp_dir("organize");
        let file = root.join("a.py");
        fs::write(&file, "print('x')\n").unwrap();
        let result = organize_or_keep(&file, &python(), "abcdef1234").unwrap();
        assert!(result.ends_with("python/a.py"), "{result:?}");
        assert!(result.is_file());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn read_only_directory_keeps_file_in_place() {
        if running_as_root() {
            eprintln!("test ignoré : root contourne les permissions");
            return;
        }
        let root = temp_dir("readonly");
        let file = root.join("b.py");
        fs::write(&file, "print('y')\n").unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o555)).unwrap();

        let result = organize_or_keep(&file, &python(), "abcdef1234");

        fs::set_permissions(&root, fs::Permissions::from_mode(0o755)).unwrap();
        let result = result.expect("l'indexation ne doit pas échouer");
        assert_eq!(result, file.canonicalize().unwrap());
        assert!(!root.join("python").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn depot_and_crawler_state_are_ignored() {
        assert!(is_ignored_path(Path::new("/data/codes/_depot/a.py")));
        assert!(is_ignored_path(Path::new("/data/codes/github/.crawler/x.json")));
        assert!(!is_ignored_path(Path::new("/data/codes/api/python/a.py")));
    }

    #[test]
    fn unwritable_probe_is_detected() {
        assert!(!directory_is_writable(Path::new("/nonexistent-icc-dir")));
    }

    #[test]
    fn language_named_folder_is_not_a_category() {
        let root = temp_dir("categories");
        let dir = root.join("crypto").join("aes").join("python");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("ctr.py");
        fs::write(&file, "x = 1\n").unwrap();
        // SAFETY : variable propre à ce test, lue seulement par la fonction testée.
        unsafe { std::env::set_var("CODECENTER_WATCH_PATHS", &root) };
        let categories = categories_for_path(&file, "python").unwrap();
        assert_eq!(categories, vec!["crypto", "aes"]);
        let _ = fs::remove_dir_all(&root);
    }
}
