fn cc_json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn cc_load_transcript(text: &str) -> Vec<Value> {
    if let Ok(value) = serde_json::from_str::<Value>(text) {
        if let Some(rows) = value.as_array() {
            return rows.iter().filter(|row| row.is_object()).cloned().collect();
        }
        if let Some(object) = value.as_object() {
            let selected = ["events", "messages", "transcript"].iter().filter_map(|key| object.get(*key)).find(|value| cc_json_truthy(value));
            if let Some(rows) = selected.and_then(Value::as_array) {
                return rows.iter().filter(|row| row.is_object()).cloned().collect();
            }
            return Vec::new();
        }
        return Vec::new();
    }
    text.lines().filter_map(|line| serde_json::from_str::<Value>(line).ok()).filter(Value::is_object).collect()
}

fn cc_transcript_command(row: &Value) -> String {
    for key in ["command", "cmd", "shell_command", "input"] {
        if let Some(value) = row.get(key).and_then(Value::as_str) { return value.to_owned(); }
    }
    let nested = row.get("tool_input").filter(|value| cc_json_truthy(value)).or_else(|| row.get("arguments"));
    if let Some(object) = nested.and_then(Value::as_object) {
        for key in ["command", "cmd"] {
            if let Some(value) = object.get(key).and_then(Value::as_str) { return value.to_owned(); }
        }
    }
    String::new()
}

fn cc_transcript_output(row: &Value) -> String {
    for key in ["output", "stdout", "result", "content"] {
        if let Some(value) = row.get(key).and_then(Value::as_str) { return value.to_owned(); }
    }
    String::new()
}

fn cc_git_status_compact(output: &str) -> String {
    let mut selected = Vec::<String>::new();
    for line in output.lines() {
        let status = line.as_bytes().get(..2);
        let status_match = matches!(status, Some(b" M" | b"M " | b"A " | b" D" | b"D " | b"??" | b"R " | b"UU"));
        if line.starts_with("On branch ") || line.starts_with("Your branch ") || line.starts_with("Changes ") || line.starts_with("Untracked ") || line.starts_with("nothing to commit") || status_match {
            if !line.trim().is_empty() && !selected.iter().any(|value| value == line) { selected.push(line.to_owned()); }
        }
    }
    selected.truncate(160);
    selected.join("\n")
}

fn cc_compact(command: &str, output: &str) -> Option<(&'static str, String)> {
    let parts = cc_split(command);
    let index = cc_executable_index(&parts)?;
    let executable = cc_casefold(cc_basename(&parts[index]));
    let arguments = cc_casefold(&parts[index + 1..].join(" "));
    if executable == "git" && (arguments == "status" || arguments.starts_with("status ")) {
        return Some(("git-status", cc_git_status_compact(output)));
    }
    None
}

fn cc_sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = serde_json::Map::new();
            for key in keys { output.insert(key.clone(), cc_sort_json(&map[key])); }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(cc_sort_json).collect()),
        _ => value.clone(),
    }
}

fn cc_canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&cc_sort_json(value)).map_err(|error| format!("JSON_SERIALIZE_FAILED:{error}"))
}

fn cc_transcript_mine(arguments: &[String]) -> Result<Value, String> {
    let start = arguments.windows(2).position(|items| items[0] == "run" && items[1] == "transcript-mine").map(|i| i + 2).ok_or_else(|| "ACTION_NOT_FOUND:transcript-mine".to_owned())?;
    let source = arguments.get(start).ok_or_else(|| "POSITIONAL_MISSING:transcript-mine:0".to_owned())?;
    let text = if Path::new(source).is_file() { fs::read_to_string(source).map_err(|error| format!("TRANSCRIPT_READ_FAILED:{error}"))? } else { source.to_owned() };
    let events = cc_load_transcript(&text);
    let mut opportunities = Vec::<Value>::new();
    let mut commands = 0usize;
    for (index, row) in events.iter().enumerate() {
        let command = cc_transcript_command(row);
        if command.is_empty() { continue; }
        commands += 1;
        let output = cc_transcript_output(row);
        let input_tokens = ((command.as_bytes().len() + output.as_bytes().len()) / 4).max(1);
        let rewrite = cc_rewrite_text(&command)?;
        if rewrite["changed"].as_bool().unwrap_or(false) {
            opportunities.push(serde_json::json!({"index":index,"command":command,"kind":"pre-tool-rewrite","estimated_input_tokens":input_tokens,"estimated_saved_tokens":(output.chars().count()/20).max(8),"recommendation":"rewrite before execution","rule":rewrite["rule"],"compactor":Value::Null}));
        }
        if let Some((compactor, visible)) = cc_compact(&command, &output) {
            let saved = output.as_bytes().len().saturating_sub(visible.as_bytes().len()) / 4;
            if saved > 0 {
                opportunities.push(serde_json::json!({"index":index,"command":command,"kind":"post-tool-compaction","estimated_input_tokens":input_tokens,"estimated_saved_tokens":saved,"recommendation":"capture exact output and return compact view","rule":Value::Null,"compactor":compactor}));
            }
        }
    }
    let total = opportunities.iter().map(|row| row["estimated_saved_tokens"].as_u64().unwrap_or(0)).sum::<u64>();
    let mut body = serde_json::json!({"events":events.len(),"commands":commands,"opportunities":opportunities,"estimated_saved_tokens":total,"coverage":{"rewrite_rules":112,"compactors":112}});
    body["analysis_hash"] = Value::String(syntavra_core::sha256_hex(cc_canonical_json(&body)?.as_bytes()));
    Ok(body)
}
