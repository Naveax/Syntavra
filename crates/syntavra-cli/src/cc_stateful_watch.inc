fn cc_watch_ignored(relative: &str) -> bool {
    let first = relative.split('/').next().unwrap_or(relative);
    matches!(first, ".git" | ".syntavra" | "node_modules" | "dist" | "build" | "__pycache__" | ".venv" | "venv")
}

fn cc_watch_scan_dir(root: &Path, dir: &Path, output: &mut serde_json::Map<String, Value>) -> Result<(), String> {
    let mut entries = fs::read_dir(dir)
        .map_err(|error| format!("WATCH_DIRECTORY_READ_FAILED:{error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("WATCH_ENTRY_FAILED:{error}"))?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        let path = entry.path();
        let kind = entry.file_type().map_err(|error| format!("WATCH_TYPE_FAILED:{error}"))?;
        let relative = path.strip_prefix(root).map_err(|_| "WATCH_RELATIVE_FAILED".to_owned())?
            .to_string_lossy().replace('\\', "/");
        if cc_watch_ignored(&relative) { continue; }
        if kind.is_dir() {
            cc_watch_scan_dir(root, &path, output)?;
        } else if kind.is_file() {
            let metadata = entry.metadata().map_err(|error| format!("WATCH_METADATA_FAILED:{error}"))?;
            let bytes = fs::read(&path).map_err(|error| format!("WATCH_FILE_READ_FAILED:{error}"))?;
            let mtime_ns = metadata.modified().ok()
                .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|duration| duration.as_nanos().min(u128::from(u64::MAX)) as u64)
                .unwrap_or(0);
            output.insert(relative.clone(), serde_json::json!({
                "path": relative,
                "size": metadata.len(),
                "mtime_ns": mtime_ns,
                "sha256": syntavra_core::sha256_hex(&bytes)
            }));
        }
    }
    Ok(())
}

fn cc_watch_scan(project: &Path) -> Result<serde_json::Map<String, Value>, String> {
    let mut output = serde_json::Map::new();
    cc_watch_scan_dir(project, project, &mut output)?;
    Ok(output)
}

fn cc_watch_snapshot_path(state_root: &Path) -> std::path::PathBuf { state_root.join("watcher/snapshot.json") }
fn cc_watch_status_path(state_root: &Path) -> std::path::PathBuf { state_root.join("watcher/status.json") }

fn cc_watch_previous(state_root: &Path) -> Result<serde_json::Map<String, Value>, String> {
    Ok(cc_stateful_read_json(&cc_watch_snapshot_path(state_root))?
        .and_then(|value| value.as_object().cloned()).unwrap_or_default())
}

fn cc_watch_poll(project: &Path, state_root: &Path, callback_result: Value) -> Result<Value, String> {
    let previous = cc_watch_previous(state_root)?;
    let current = cc_watch_scan(project)?;
    let mut added = Vec::<String>::new();
    let mut modified = Vec::<String>::new();
    let mut deleted = Vec::<String>::new();
    for (path, row) in &current {
        match previous.get(path) {
            None => added.push(path.clone()),
            Some(old) if old.get("sha256") != row.get("sha256") => modified.push(path.clone()),
            _ => {}
        }
    }
    for path in previous.keys() { if !current.contains_key(path) { deleted.push(path.clone()); } }
    added.sort(); modified.sort(); deleted.sort();
    let change = serde_json::json!({
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "scanned": current.len(),
        "timestamp": cc_stateful_now()?
    });
    cc_stateful_write_json(&cc_watch_snapshot_path(state_root), &Value::Object(current))?;
    cc_stateful_write_json(&cc_watch_status_path(state_root), &serde_json::json!({
        "running": false,
        "last_poll": change,
        "callback_result": callback_result
    }))?;
    Ok(change)
}

fn cc_watch_action(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let iterations = cc_stateful_option_i64(arguments, "--iterations", 1)?.max(0) as usize;
    let interval = cc_stateful_option_f64(arguments, "--interval", 1.0)?;
    if interval <= 0.0 { return Err("interval_seconds must be positive".to_owned()); }
    let no_index = arguments.iter().any(|item| item == "--no-index");
    cc_stateful_write_json(&cc_watch_status_path(state_root), &serde_json::json!({
        "running": true,
        "pid": std::process::id(),
        "started_at": cc_stateful_now()?,
        "interval_seconds": interval
    }))?;
    let mut rows = Vec::<Value>::new();
    for index in 0..iterations {
        let change = cc_watch_poll(project, state_root, Value::Null)?;
        if !no_index {
            let changed = change["added"].as_array().map_or(0, Vec::len)
                + change["modified"].as_array().map_or(0, Vec::len)
                + change["deleted"].as_array().map_or(0, Vec::len);
            if changed > 0 {
                let command = vec!["run".to_owned(), "graph-index".to_owned()];
                let args = vec!["run".to_owned(), "graph-index".to_owned()];
                let _ = native_remaining71_graph::execute(&command, &args, project, state_root);
            }
        }
        rows.push(change);
        if index + 1 < iterations { std::thread::sleep(std::time::Duration::from_secs_f64(interval)); }
    }
    let mut status = cc_stateful_read_json(&cc_watch_status_path(state_root))?.unwrap_or_else(|| serde_json::json!({"running":false}));
    if let Some(map) = status.as_object_mut() {
        map.insert("running".to_owned(), Value::Bool(false));
        map.insert("stopped_at".to_owned(), serde_json::json!(cc_stateful_now()?));
        map.insert("iterations".to_owned(), serde_json::json!(iterations));
    }
    cc_stateful_write_json(&cc_watch_status_path(state_root), &status)?;
    Ok(serde_json::json!({"changes":rows,"status":status}))
}
