use serde::Serialize;
use sqlx::Row;

use super::pool;

#[derive(Debug, Serialize)]
pub struct DatabaseStats {
    pub documents_total: i64,
    pub total_size_bytes: i64,
    pub analysis_pending: i64,
    pub analysis_processing: i64,
    pub analysis_completed: i64,
    pub analysis_failed: i64,
}

/// Calcule les statistiques des documents enregistrés.
pub async fn get_stats() -> Result<DatabaseStats, sqlx::Error> {
    let row = sqlx::query(
        r#"
        SELECT
            COUNT(*)::BIGINT AS documents_total,
            COALESCE(SUM(file_size), 0)::BIGINT AS total_size_bytes,
            COUNT(*) FILTER (
                WHERE analysis_status = 'pending'
            )::BIGINT AS analysis_pending,
            COUNT(*) FILTER (
                WHERE analysis_status = 'processing'
            )::BIGINT AS analysis_processing,
            COUNT(*) FILTER (
                WHERE analysis_status = 'completed'
            )::BIGINT AS analysis_completed,
            COUNT(*) FILTER (
                WHERE analysis_status = 'failed'
            )::BIGINT AS analysis_failed
        FROM code_examples
        "#,
    )
    .fetch_one(pool()?)
    .await?;

    Ok(DatabaseStats {
        documents_total: row.try_get("documents_total")?,
        total_size_bytes: row.try_get("total_size_bytes")?,
        analysis_pending: row.try_get("analysis_pending")?,
        analysis_processing: row.try_get("analysis_processing")?,
        analysis_completed: row.try_get("analysis_completed")?,
        analysis_failed: row.try_get("analysis_failed")?,
    })
}