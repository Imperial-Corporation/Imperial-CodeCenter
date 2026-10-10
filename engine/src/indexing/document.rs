use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use crate::database::documents::upsert_document;
use crate::indexing::fingerprint::hash_content;

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

    for component in relative.components() {
        current.push(component);

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

/// Indexe un fichier, le range par langage et synchronise PostgreSQL.
pub async fn index_file(
    path: &Path,
) -> Result<bool, Box<dyn Error + Send + Sync>> {
    if !path.is_file() {
        return Ok(false);
    }

    let extension = path
        .extension()
        .and_then(|ext| ext.to_str())
        .ok_or("Extension absente")?
        .to_ascii_lowercase();

    let language = language_for_extension(&extension)
        .ok_or_else(|| format!("Langage non pris en charge : {extension}"))?;

    let original_content = fs::read(path)?;
    let content_sha256 = hash_content(&original_content);

    let organized_path = organize_file(path, &language, &content_sha256)?;

    let content = fs::read(&organized_path)?;
    let content_sha256 = hash_content(&content);
    let file_size = i64::try_from(content.len())?;

    let storage_path = organized_path.to_string_lossy().into_owned();
    let filename = organized_path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown");

    let categories = categories_for_path(&organized_path)?;

    upsert_document(
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
    .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)
}

/// Indique si l'extension est prise en charge.
pub fn is_supported_file(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .and_then(language_for_extension)
        .is_some()
}
