fn cc_dashboard_memory(state_root:&Path)->Value{
    let path=state_root.join("memory-intelligence.sqlite3");
    if !path.is_file(){return serde_json::json!({"observations":0,"valid":0,"missing_embeddings":0})}
    let Ok(connection)=rusqlite::Connection::open(path) else{return serde_json::json!({"observations":0,"valid":0,"missing_embeddings":0})};
    let observations=connection.query_row("SELECT COUNT(*) FROM observations",[],|r|r.get::<_,i64>(0)).unwrap_or(0);
    serde_json::json!({"observations":observations,"valid":0,"missing_embeddings":0})
}

fn cc_dashboard_notifications(state_root:&Path)->usize{
    fs::read_to_string(state_root.join("notifications/events.jsonl")).ok().map_or(0,|text|text.lines().filter(|line|serde_json::from_str::<Value>(line).is_ok()).count())
}

fn cc_dashboard_snapshot(project:&Path,state_root:&Path)->Result<Value,String>{
    let mode=native_mode::execute(&["run".to_owned(),"mode".to_owned()],state_root)?;
    let status=native_statusline::execute(&["run".to_owned(),"statusline".to_owned()],state_root)?;
    let basic=status["savings"].clone();
    let savings=serde_json::json!({
        "events":basic["events"],"original_tokens":basic["original_tokens"],"visible_tokens":basic["visible_tokens"],"saved_tokens":basic["saved_tokens"],"savings_ratio":basic["savings_ratio"],
        "measurement_basis":"LOCAL_MODEL_VISIBLE_ESTIMATE","provider_cost_before":basic["provider_cost_before"],"provider_cost_after":basic["provider_cost_after"],"provider_cost_saved":basic["provider_cost_saved"],
        "provider_cost_observations":0,"net_provider_savings_proven":false,"claim_boundary":"paired provider-observed receipts plus verifier-success proof gate required for net token/cost/latency claims","by_source":basic["by_source"]
    });
    let cache_command=vec!["run".to_owned(),"cache-health".to_owned()];
    let cache_args=cache_command.clone();
    let cache=native_remaining71_competitive::execute(&cache_command,&cache_args,project,state_root)?.unwrap_or_else(||serde_json::json!({"active":0,"cacheable_tokens":0,"expired":0,"plans":0,"refresh_due":0}));
    let saved=savings["saved_tokens"].as_i64().unwrap_or(0);
    let saved_text=if saved>=1_000_000{format!("{:.1}m",saved as f64/1_000_000.0)}else if saved>=1_000{format!("{:.1}k",saved as f64/1_000.0)}else{saved.to_string()};
    Ok(serde_json::json!({
        "statusline":format!("[SYN:{}] local⇩{}",mode["active"]["name"].as_str().unwrap_or("full").to_uppercase(),saved_text),
        "mode":mode,"savings":savings,"cache":cache,"memory":cc_dashboard_memory(state_root),"worker":cc_worker_status(state_root)?,
        "notifications":cc_dashboard_notifications(state_root),"timestamp":cc_stateful_now()?
    }))
}
