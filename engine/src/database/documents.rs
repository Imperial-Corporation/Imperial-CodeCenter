use sqlx::Error;
use uuid::Uuid;

use super::get_pool;

/// Enregistre un document ou actualise son entrée si nécessaire.
///
/// Retourne `true` si la base a été modifiée, `false` sinon.
pub async fn upsert_document(
    storage_path: &str,
    filename: &str,
    content_sha256: &str,
    file_size: i64,
) -> Result<bool, Error> {
    let id = Uuid::new_v4();

    let result = sqlx::query(
        r#"
        INSERT INTO code_examples (
            id,
            title,
            storage_path,
            original_filename,
            content_sha256,
            file_size,
            analysis_status
        )
        VALUES ($1, $2, $3, $4, $5, $6, 'pending')
        ON CONFLICT (storage_path) DO UPDATE SET
            title = EXCLUDED.title,
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
        WHERE
            code_examples.content_sha256
                IS DISTINCT FROM EXCLUDED.content_sha256
            OR code_examples.file_size
                IS DISTINCT FROM EXCLUDED.file_size
            OR code_examples.original_filename
                IS DISTINCT FROM EXCLUDED.original_filename
        "#,
    )
    .bind(id)
    .bind(filename)
    .bind(storage_path)
    .bind(filename)
    .bind(content_sha256)
    .bind(file_size)
    .execute(get_pool().await?)
    .await?;

    Ok(result.rows_affected() > 0)
}

/// Supprime un document identifié par son chemin.
///
/// Retourne `true` si un document a été supprimé.
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