fn cc_worker_action(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let start = cc_stateful_action_index(arguments, "worker").ok_or_else(|| "ACTION_NOT_FOUND:worker".to_owned())?;
    let action = arguments.get(start).map(String::as_str).unwrap_or_default();
    if action == "status" { return cc_worker_status(state_root); }
    if action != "run" { return Err(format!("WORKER_ACTION_INVALID:{action}")); }
    let iterations = cc_stateful_option_i64(arguments, "--iterations", 1)?.max(0) as usize;
    let interval = cc_stateful_option_f64(arguments, "--interval", 2.0)?;
    if interval <= 0.0 { return Err("interval_seconds must be positive".to_owned()); }
    cc_worker_memory_init(state_root)?;
    let status_path = cc_worker_status_path(state_root);
    cc_stateful_write_json(&status_path, &serde_json::json!({
        "running": true,"pid": std::process::id(),"started_at": cc_stateful_now()?,"interval_seconds": interval
    }))?;
    let mut cycles = Vec::<Value>::new();
    for index in 0..iterations {
        let change = cc_watch_poll(project, state_root, Value::Null)?;
        let changed = change["added"].as_array().map_or(0, Vec::len)
            + change["modified"].as_array().map_or(0, Vec::len)
            + change["deleted"].as_array().map_or(0, Vec::len);
        if changed > 0 {
            let command = vec!["run".to_owned(), "graph-index".to_owned()];
            let args = vec!["run".to_owned(), "graph-index".to_owned()];
            let _ = native_remaining71_graph::execute(&command, &args, project, state_root);
        }
        let mut cycle = serde_json::json!({
            "timestamp": cc_stateful_now()?,"changes": change,
            "embeddings":{"embedded":0,"remaining":0},
            "memory":{"observations":0,"valid":0,"missing_embeddings":0}
        });
        cycle["cycle_hash"] = Value::String(syntavra_core::sha256_hex(cc_canonical_json(&cycle)?.as_bytes()));
        cycles.push(cycle.clone());
        cc_stateful_write_json(&status_path, &serde_json::json!({
            "running":true,"pid":std::process::id(),"iterations":index+1,"last_cycle":cycle,"interval_seconds":interval
        }))?;
        if index + 1 < iterations { std::thread::sleep(std::time::Duration::from_secs_f64(interval)); }
    }
    let mut status = cc_stateful_read_json(&status_path)?.unwrap_or_else(|| serde_json::json!({}));
    if let Some(map) = status.as_object_mut() {
        map.insert("running".to_owned(), Value::Bool(false));
        map.insert("stopped_at".to_owned(), serde_json::json!(cc_stateful_now()?));
        map.insert("iterations".to_owned(), serde_json::json!(iterations));
    }
    cc_stateful_write_json(&status_path, &status)?;
    Ok(serde_json::json!({"ok":true,"iterations":iterations,"cycles":cycles,"status":status}))
}
