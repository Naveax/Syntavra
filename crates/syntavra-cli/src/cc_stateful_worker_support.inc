fn cc_worker_status_path(state_root: &Path) -> std::path::PathBuf { state_root.join("workers/intelligence.json") }

fn cc_worker_memory_init(state_root: &Path) -> Result<(), String> {
    let path = state_root.join("memory-intelligence.sqlite3");
    let connection = rusqlite::Connection::open(path).map_err(|error| format!("WORKER_MEMORY_OPEN_FAILED:{error}"))?;
    connection.execute_batch("CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY);")
        .map_err(|error| format!("WORKER_MEMORY_INIT_FAILED:{error}"))
}

fn cc_worker_status(state_root: &Path) -> Result<Value, String> {
    Ok(cc_stateful_read_json(&cc_worker_status_path(state_root))?
        .unwrap_or_else(|| serde_json::json!({"running":false,"initialized":false})))
}
