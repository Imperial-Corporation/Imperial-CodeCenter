use pyo3::exceptions::PyNotImplementedError;
use pyo3::prelude::*;


// --------------------------------------------------
// Santé du moteur
// --------------------------------------------------

#[pyfunction]
fn healt() -> String {
    String::from("Rust System is Working !")
}


// --------------------------------------------------
// Recherche
// --------------------------------------------------

#[pyfunction]
fn search(
    query: &str,
    limit: usize,
    language: Option<&str>,
) -> PyResult<String> {
    let _ = (query, limit, language);

    Err(PyNotImplementedError::new_err(
        "search n'est pas encore implémentée",
    ))
}


// --------------------------------------------------
// Documents
// --------------------------------------------------

#[pyfunction]
fn index_document(
    document_id: &str,
    content: &str,
    language: Option<&str>,
) -> PyResult<bool> {
    let _ = (document_id, content, language);

    Err(PyNotImplementedError::new_err(
        "index_document n'est pas encore implémentée",
    ))
}


#[pyfunction]
fn remove_document(
    document_id: &str,
) -> PyResult<bool> {
    let _ = document_id;

    Err(PyNotImplementedError::new_err(
        "remove_document n'est pas encore implémentée",
    ))
}


#[pyfunction]
fn rebuild_index() -> PyResult<String> {
    Err(PyNotImplementedError::new_err(
        "rebuild_index n'est pas encore implémentée",
    ))
}


// --------------------------------------------------
// Statistiques
// --------------------------------------------------

#[pyfunction]
fn get_stats() -> PyResult<String> {
    Err(PyNotImplementedError::new_err(
        "get_stats n'est pas encore implémentée",
    ))
}


// --------------------------------------------------
// Module Python
// --------------------------------------------------

#[pymodule]
fn codecenter_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(healt, m)?)?;

    m.add_function(wrap_pyfunction!(search, m)?)?;

    m.add_function(wrap_pyfunction!(index_document, m)?)?;
    m.add_function(wrap_pyfunction!(remove_document, m)?)?;
    m.add_function(wrap_pyfunction!(rebuild_index, m)?)?;

    m.add_function(wrap_pyfunction!(get_stats, m)?)?;

    Ok(())
}