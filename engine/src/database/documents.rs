use std::path::Path;

use sqlx::{Error, Row, Transaction, Postgres};
use uuid::Uuid;

use super::get_pool;

/// Enregistre un document, reconnaît un déplacement non ambigu
/// et synchronise ses métadonnées dans une transaction.
pub async fn upsert_document(
    storage_path: &str,
    filename: &str,
    content_sha256: &str,
    file_size: i64,
    language_name: &str,
    language_slug: &str,
    extension: &str,
    categories: &[String],
) -> Result<bool, Error> {
    let pool = get_pool().await?;
    let mut tx = pool.begin().await?;

    let language_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO languages (name, slug, file_extensions)
        VALUES ($1, $2, ARRAY[$3]::TEXT[])
        ON CONFLICT (slug) DO UPDATE SET
            name = EXCLUDED.name,
            file_extensions = (
                SELECT ARRAY(
                    SELECT DISTINCT value
                    FROM unnest(
                        languages.file_extensions || EXCLUDED.file_extensions
                    ) AS value
                    ORDER BY value
                )
            )
        RETURNING id
        "#,
    )
    .bind(language_name)
    .bind(language_slug)
    .bind(extension)
    .fetch_one(&mut *tx)
    .await?;

    let existing_id: Option<Uuid> = sqlx::query_scalar(
        "SELECT id FROM code_examples WHERE storage_path = $1",
    )
    .bind(storage_path)
    .fetch_optional(&mut *tx)
    .await?;

    let document_id = if let Some(id) = existing_id {
        id
    } else {
        // Un déplacement est reconnu uniquement si un seul document
        // possède le même SHA-256 et nom, et que son ancien chemin
        // n'existe plus sur le disque.
        let candidates = sqlx::query(
            r#"
            SELECT id, storage_path
            FROM code_examples
            WHERE content_sha256 = $1
              AND original_filename = $2
              AND storage_path <> $3
            FOR UPDATE
            "#,
        )
        .bind(content_sha256)
        .bind(filename)
        .bind(storage_path)
        .fetch_all(&mut *tx)
        .await?;

        let missing: Vec<Uuid> = candidates
            .into_iter()
            .filter(|row| {
                let old_path: String = row.get("storage_path");
                !Path::new(&old_path).exists()
            })
            .map(|row| row.get("id"))
            .collect();

        if missing.len() == 1 {
            missing[0]
        } else {
            Uuid::new_v4()
        }
    };

    
    // Libère l'ancien chemin avant la réinsertion du document déplacé.
    // Si l'UUID vient d'être créé, cette mise à jour ne modifie aucune ligne.
    if existing_id.is_none() {
        sqlx::query(
            r#"
            UPDATE code_examples
            SET storage_path = $2
            WHERE id = $1
              AND storage_path <> $2
            "#,
        )
        .bind(document_id)
        .bind(storage_path)
        .execute(&mut *tx)
        .await?;
    }

    
    let result = sqlx::query(
        r#"
        INSERT INTO code_examples (
            id, title, language_id, storage_path,
            original_filename, content_sha256, file_size,
            analysis_status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
        ON CONFLICT (storage_path) DO UPDATE SET
            title = EXCLUDED.title,
            language_id = EXCLUDED.language_id,
            original_filename = EXCLUDED.original_filename,
            content_sha256 = EXCLUDED.content_sha256,
            file_size = EXCLUDED.file_size,
            analysis_status = CASE
                WHEN code_examples.content_sha256
                    IS DISTINCT FROM EXCLUDED.content_sha256
                THEN 'pending'
                ELSE code_examples.analysis_status
            END,
            analysis_error = CASE
                WHEN code_examples.content_sha256
                    IS DISTINCT FROM EXCLUDED.content_sha256
                THEN NULL
                ELSE code_examples.analysis_error
            END,
            updated_at = NOW()
        "#,
    )
    .bind(document_id)
    .bind(filename)
    .bind(language_id)
    .bind(storage_path)
    .bind(filename)
    .bind(content_sha256)
    .bind(file_size)
    .execute(&mut *tx)
    .await?;

    // Un document déplacé conserve son UUID : on met à jour son chemin.
    sqlx::query(
        r#"
        UPDATE code_examples
        SET storage_path = $2,
            language_id = $3,
            title = $4,
            original_filename = $5,
            file_size = $6,
            updated_at = NOW()
        WHERE id = $1
          AND storage_path <> $2
        "#,
    )
    .bind(document_id)
    .bind(storage_path)
    .bind(language_id)
    .bind(filename)
    .bind(filename)
    .bind(file_size)
    .execute(&mut *tx)
    .await?;

    // Reconstruit les associations de catégories du document.
    sqlx::query(
        "DELETE FROM code_example_categories WHERE code_example_id = $1",
    )
    .bind(document_id)
    .execute(&mut *tx)
    .await?;

    let mut parent_id: Option<i64> = None;

    for category_name in categories {
        let slug = category_slug(category_name);

        let category_id = find_or_create_category(
            &mut tx,
            category_name,
            &slug,
            parent_id,
        )
        .await?;

        sqlx::query(
            r#"
            INSERT INTO code_example_categories (code_example_id, category_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            "#,
        )
        .bind(document_id)
        .bind(category_id)
        .execute(&mut *tx)
        .await?;

        parent_id = Some(category_id);
    }

    tx.commit().await?;

    Ok(result.rows_affected() > 0)
}

async fn find_or_create_category(
    tx: &mut Transaction<'_, Postgres>,
    name: &str,
    slug: &str,
    parent_id: Option<i64>,
) -> Result<i64, Error> {
    let existing: Option<i64> = sqlx::query_scalar(
        r#"
        SELECT id FROM categories
        WHERE parent_id IS NOT DISTINCT FROM $1
          AND slug = $2
        "#,
    )
    .bind(parent_id)
    .bind(slug)
    .fetch_optional(&mut **tx)
    .await?;

    if let Some(id) = existing {
        return Ok(id);
    }

    sqlx::query(
        r#"
        INSERT INTO categories (name, slug, parent_id)
        VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(name)
    .bind(slug)
    .bind(parent_id)
    .execute(&mut **tx)
    .await?;

    sqlx::query_scalar(
        r#"
        SELECT id FROM categories
        WHERE parent_id IS NOT DISTINCT FROM $1
          AND slug = $2
        "#,
    )
    .bind(parent_id)
    .bind(slug)
    .fetch_one(&mut **tx)
    .await
}

fn category_slug(name: &str) -> String {
    let mut slug = String::new();
    let mut separator = false;

    for character in name.chars().flat_map(char::to_lowercase) {
        if character.is_alphanumeric() {
            slug.push(character);
            separator = false;
        } else if !separator && !slug.is_empty() {
            slug.push('-');
            separator = true;
        }
    }

    slug.trim_matches('-').to_owned()
}

/// Supprime un document identifié par son chemin.
pub async fn remove_document_by_path(
    storage_path: &str,
) -> Result<bool, Error> {
    let result = sqlx::query(
        "DELETE FROM code_examples WHERE storage_path = $1",
    )
    .bind(storage_path)
    .execute(get_pool().await?)
    .await?;

    Ok(result.rows_affected() > 0)
}
