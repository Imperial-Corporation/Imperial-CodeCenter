use pyo3::prelude::*;

use std::sync::{Mutex, OnceLock};
use std::thread::JoinHandle;

mod database;
mod indexing;
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

#[pyfunction]
fn search(
    query: &str,
    limit: usize,
    language: Option<&str>,
) -> PyResult<String> {
    let _ = (query, limit, language);

    Err(pyo3::exceptions::PyNotImplementedError::new_err(
        "search n'est pas encore implémentée",
    ))
}

#[pyfunction]
fn get_stats(py: Python<'_>) -> PyResult<String> {
    py.detach(get_stats_json)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pymodule]
fn codecenter_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(healt, m)?)?;
    m.add_function(wrap_pyfunction!(search, m)?)?;
    m.add_function(wrap_pyfunction!(get_stats, m)?)?;
    m.add_function(wrap_pyfunction!(start_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(stop_watcher, m)?)?;

    Ok(())
}