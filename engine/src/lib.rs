use pyo3::prelude::*;

#[pyfunction]
fn healt() -> String {
    String::from("Rust System is Working !")
}

#[pymodule]
fn codecenter_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(healt, m)?)?;
    Ok(())
}
