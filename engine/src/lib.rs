use pyo3::prelude::*;

use std::sync::{Mutex, OnceLock};
use std::thread::JoinHandle;

mod database;
mod indexing;
mod search;
mod watcher;

use crate::database::get_stats_json;

struct WatcherHandle {
    shutdown: tokio::sync::watch::Sender<bool>,
    thread: JoinHandle<()>,
}

static WATCHER: OnceLock<Mutex<Option<WatcherHandle>>> = OnceLock::new();

fn watcher_slot() -> &'static Mutex<Option<WatcherHandle>> {
    WATCHER.get_or_init(|| Mutex::new(None))
}

#[pyfunction]
fn start_watcher() -> PyResult<()> {
    let mut slot = watcher_slot().lock().map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err(
            "Impossible d'accéder à l'état du watcher.",
        )
    })?;

    if slot
        .as_ref()
        .is_some_and(|watcher| !watcher.thread.is_finished())
    {
        return Ok(());
    }

    if let Some(previous) = slot.take() {
        previous.thread.join().map_err(|_| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "Le précédent thread du watcher s'est terminé anormalement.",
            )
        })?;
    }

    let (shutdown, receiver) = tokio::sync::watch::channel(false);

    let thread = std::thread::Builder::new()
        .name("codecenter-watcher".into())
        .spawn(move || {
            let runtime = match tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
            {
                Ok(runtime) => runtime,
                Err(error) => {
                    eprintln!(
                        "CodeCenter : impossible de créer le runtime Tokio : {error}"
                    );
                    return;
                }
            };

            if let Err(error) = runtime.block_on(watcher::run(receiver)) {
                eprintln!("CodeCenter : erreur du watcher : {error}");
            }
        })
        .map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Impossible de créer le thread du watcher : {error}"
            ))
        })?;

    *slot = Some(WatcherHandle { shutdown, thread });

    Ok(())
}

#[pyfunction]
fn stop_watcher() -> PyResult<()> {
    let watcher = watcher_slot()
        .lock()
        .map_err(|_| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "Impossible d'accéder à l'état du watcher.",
            )
        })?
        .take();

    if let Some(watcher) = watcher {
        let _ = watcher.shutdown.send(true);

        watcher.thread.join().map_err(|_| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "Le thread du watcher s'est terminé anormalement.",
            )
        })?;
    }

    Ok(())
}

#[pyfunction]
fn healt() -> String {
    String::from("Rust System is Working !")
}

/// Traduit une erreur de recherche en exception Python typée :
/// ValueError (invalide), LookupError (introuvable), PermissionError
/// (chemin interdit) ou RuntimeError (interne).
fn to_py_error(error: search::SearchError) -> PyErr {
    use pyo3::exceptions::{PyLookupError, PyPermissionError, PyRuntimeError, PyValueError};
    use search::SearchError;

    match error {
        SearchError::Invalid(message) => PyValueError::new_err(message),
        SearchError::NotFound(message) => PyLookupError::new_err(message),
        SearchError::Forbidden(message) => PyPermissionError::new_err(message),
        SearchError::Internal(message) => PyRuntimeError::new_err(message),
    }
}

/// Recherche de documents ; retourne un JSON (voir `search::SearchOutput`).
#[pyfunction(name = "search")]
#[pyo3(signature = (query, limit=10, language=None, category=None))]
fn search_documents(
    py: Python<'_>,
    query: &str,
    limit: usize,
    language: Option<&str>,
    category: Option<&str>,
) -> PyResult<String> {
    py.detach(|| search::search_json(query, limit, language, category))
        .map_err(to_py_error)
}

/// Lit un document indexé (plage de lignes et plafond d'octets optionnels).
#[pyfunction]
#[pyo3(signature = (document_id, start_line=None, end_line=None, max_bytes=None))]
fn get_document(
    py: Python<'_>,
    document_id: &str,
    start_line: Option<usize>,
    end_line: Option<usize>,
    max_bytes: Option<usize>,
) -> PyResult<String> {
    py.detach(|| search::get_document_json(document_id, start_line, end_line, max_bytes))
        .map_err(to_py_error)
}

/// Langages et catégories réellement présents (filtres disponibles).
#[pyfunction]
fn list_filters(py: Python<'_>) -> PyResult<String> {
    py.detach(search::list_filters_json).map_err(to_py_error)
}

#[pyfunction]
fn get_stats(py: Python<'_>) -> PyResult<String> {
    py.detach(get_stats_json)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

/// Santé de l'indexation : échecs, dossiers non inscriptibles, fichiers ignorés.
#[pyfunction]
fn indexing_issues() -> String {
    indexing::health::report_json()
}

#[pymodule]
fn codecenter_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(healt, m)?)?;
    m.add_function(wrap_pyfunction!(search_documents, m)?)?;
    m.add_function(wrap_pyfunction!(get_document, m)?)?;
    m.add_function(wrap_pyfunction!(list_filters, m)?)?;
    m.add_function(wrap_pyfunction!(get_stats, m)?)?;
    m.add_function(wrap_pyfunction!(indexing_issues, m)?)?;
    m.add_function(wrap_pyfunction!(start_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(stop_watcher, m)?)?;

    Ok(())
}