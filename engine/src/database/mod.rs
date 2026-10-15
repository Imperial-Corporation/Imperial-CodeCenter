use std::sync::OnceLock;
use std::time::Duration;

use sqlx::postgres::{PgPool, PgPoolOptions};
use tokio::runtime::Runtime;

pub mod stats;
pub mod documents;

static DATABASE: OnceLock<PgPool> = OnceLock::new();
static RUNTIME: OnceLock<Runtime> = OnceLock::new();

/// Initialise la connexion PostgreSQL une seule fois.
async fn initialize() -> Result<(), sqlx::Error> {
    if DATABASE.get().is_some() {
        return Ok(());
    }

    let database_url = std::env::var("DATABASE_URL").map_err(|_| {
        sqlx::Error::Configuration(
            std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "La variable DATABASE_URL est absente.",
            )
            .into(),
        )
    })?;

    let pool = PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(5))
        .connect(&database_url)
        .await?;

    // Si une autre initialisation a gagné la course, son pool est conservé.
    let _ = DATABASE.set(pool);

    Ok(())
}

/// Retourne le pool PostgreSQL partagé.
pub fn pool() -> Result<&'static PgPool, sqlx::Error> {
    DATABASE.get().ok_or_else(|| {
        sqlx::Error::Configuration(
            std::io::Error::new(
                std::io::ErrorKind::NotConnected,
                "La connexion PostgreSQL n'est pas initialisée.",
            )
            .into(),
        )
    })
}

/// Initialise PostgreSQL si nécessaire et retourne le pool partagé.
pub async fn get_pool() -> Result<&'static PgPool, sqlx::Error> {
    initialize().await?;
    pool()
}

/// Runtime Tokio partagé par les appels bloquants exposés à Python.
pub fn runtime() -> &'static Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("Impossible de créer le runtime Tokio")
    })
}

/// Récupère les statistiques et les sérialise en JSON pour Python.
pub fn get_stats_json() -> Result<String, String> {
    runtime().block_on(async {
        initialize()
            .await
            .map_err(|error| error.to_string())?;

        let result = stats::get_stats()
            .await
            .map_err(|error| error.to_string())?;

        serde_json::to_string(&result)
            .map_err(|error| error.to_string())
    })
}