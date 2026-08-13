include!("cc_checkpoint_compactor_registry.inc");

fn cc_json_token_boundary(bytes: &[u8], end: usize) -> bool {
    bytes
        .get(end)
        .map_or(true, |byte| !byte.is_ascii_alphanumeric() && *byte != b'_')
}

fn cc_python_json_compat(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut output = Vec::<u8>::with_capacity(bytes.len());
    let mut index = 0usize;
    let mut quoted = false;
    let mut escaped = false;
    while index < bytes.len() {
        let byte = bytes[index];
        if quoted {
            output.push(byte);
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
            index += 1;
            continue;
        }
        if byte == b'"' {
            quoted = true;
            output.push(byte);
            index += 1;
            continue;
        }
        let rest = &bytes[index..];
        if rest.starts_with(b"-Infinity") && cc_json_token_boundary(bytes, index + 9) {
            output.extend_from_slice(b"true");
            index += 9;
            continue;
        }
        if rest.starts_with(b"Infinity") && cc_json_token_boundary(bytes, index + 8) {
            output.extend_from_slice(b"true");
            index += 8;
            continue;
        }
        if rest.starts_with(b"NaN") && cc_json_token_boundary(bytes, index + 3) {
            output.extend_from_slice(b"true");
            index += 3;
            continue;
        }
        output.push(byte);
        index += 1;
    }
    String::from_utf8(output).unwrap_or_else(|_| text.to_owned())
}

fn cc_parse_python_json(text: &str) -> Result<Value, serde_json::Error> {
    serde_json::from_str::<Value>(&cc_python_json_compat(text))
}

include!("cc_checkpoint_rewrite.inc");
include!("cc_checkpoint_transcript.inc");

fn cc_load_transcript_compat(text: &str) -> Vec<Value> {
    if let Ok(value) = cc_parse_python_json(text) {
        if let Some(rows) = value.as_array() {
            return rows.iter().filter(|row| row.is_object()).cloned().collect();
        }
        if let Some(object) = value.as_object() {
            let selected = ["events", "messages", "transcript"]
                .iter()
                .filter_map(|key| object.get(*key))
                .find(|value| cc_json_truthy(value));
            if let Some(rows) = selected.and_then(Value::as_array) {
                return rows.iter().filter(|row| row.is_object()).cloned().collect();
            }
            return Vec::new();
        }
        return Vec::new();
    }
    text.lines()
        .filter_map(|line| cc_parse_python_json(line).ok())
        .filter(Value::is_object)
        .collect()
}

fn cc_transcript_mine_compat(arguments: &[String]) -> Result<Value, String> {
    let start = arguments
        .windows(2)
        .position(|items| items[0] == "run" && items[1] == "transcript-mine")
        .map(|index| index + 2)
        .ok_or_else(|| "ACTION_NOT_FOUND:transcript-mine".to_owned())?;
    let source = arguments
        .get(start)
        .ok_or_else(|| "POSITIONAL_MISSING:transcript-mine:0".to_owned())?;
    let text = if Path::new(source).is_file() {
        fs::read_to_string(source).map_err(|error| format!("TRANSCRIPT_READ_FAILED:{error}"))?
    } else {
        source.to_owned()
    };
    let events = cc_load_transcript_compat(&text);
    let mut opportunities = Vec::<Value>::new();
    let mut commands = 0usize;
    for (index, row) in events.iter().enumerate() {
        let command = cc_transcript_command(row);
        if command.is_empty() {
            continue;
        }
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
    let total = opportunities
        .iter()
        .map(|row| row["estimated_saved_tokens"].as_u64().unwrap_or(0))
        .sum::<u64>();
    let mut body = serde_json::json!({"events":events.len(),"commands":commands,"opportunities":opportunities,"estimated_saved_tokens":total,"coverage":{"rewrite_rules":REWRITE_RULE_COUNT,"compactors":COMPACTOR_COUNT}});
    body["analysis_hash"] = Value::String(syntavra_core::sha256_hex(
        cc_canonical_json(&body)?.as_bytes(),
    ));
    Ok(body)
}

fn cc_checkpoint_supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && matches!(action.as_str(), "rewrite" | "transcript-mine"))
}

fn cc_checkpoint_execute(command: &[String], arguments: &[String]) -> Result<Option<Value>, String> {
    if !cc_checkpoint_supports(command) {
        return Ok(None);
    }
    match command[1].as_str() {
        "rewrite" => cc_rewrite_action(arguments).map(Some),
        "transcript-mine" => cc_transcript_mine_compat(arguments).map(Some),
        _ => Ok(None),
    }
}
