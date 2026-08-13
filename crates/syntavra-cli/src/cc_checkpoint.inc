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

const CC_JSON_NAN: &str = "__SYNTAVRA_CC_NAN__";
const CC_JSON_INF: &str = "__SYNTAVRA_CC_INF__";
const CC_JSON_NINF: &str = "__SYNTAVRA_CC_NINF__";
const CC_JSON_INT: &str = "__SYNTAVRA_CC_INT__";

fn cc_push_json_marker(output: &mut Vec<u8>, marker: &str) {
    output.push(b'"');
    output.extend_from_slice(marker.as_bytes());
    output.push(b'"');
}

fn cc_json_number_delimiter(byte: u8) -> bool {
    matches!(byte, b',' | b']' | b'}' | b' ' | b'\t' | b'\r' | b'\n')
}

fn cc_compactor_json_compat(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut output = Vec::<u8>::with_capacity(bytes.len() + 64);
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
            cc_push_json_marker(&mut output, CC_JSON_NINF);
            index += 9;
            continue;
        }
        if rest.starts_with(b"Infinity") && cc_json_token_boundary(bytes, index + 8) {
            cc_push_json_marker(&mut output, CC_JSON_INF);
            index += 8;
            continue;
        }
        if rest.starts_with(b"NaN") && cc_json_token_boundary(bytes, index + 3) {
            cc_push_json_marker(&mut output, CC_JSON_NAN);
            index += 3;
            continue;
        }
        let numeric_start = byte.is_ascii_digit()
            || (byte == b'-' && bytes.get(index + 1).is_some_and(u8::is_ascii_digit));
        if numeric_start {
            let start = index;
            index += 1;
            while index < bytes.len() && !cc_json_number_delimiter(bytes[index]) {
                index += 1;
            }
            let token = std::str::from_utf8(&bytes[start..index]).unwrap_or("");
            if token == "-0" {
                output.push(b'0');
                continue;
            }
            let integer = !token.contains(['.', 'e', 'E']);
            if integer && token.parse::<i64>().is_err() && token.parse::<u64>().is_err() {
                cc_push_json_marker(&mut output, &format!("{CC_JSON_INT}{token}"));
                continue;
            }
            if !integer {
                if let Ok(number) = token.parse::<f64>() {
                    if number.is_infinite() {
                        cc_push_json_marker(
                            &mut output,
                            if number.is_sign_negative() { CC_JSON_NINF } else { CC_JSON_INF },
                        );
                        continue;
                    }
                }
            }
            output.extend_from_slice(&bytes[start..index]);
            continue;
        }
        output.push(byte);
        index += 1;
    }
    String::from_utf8(output).unwrap_or_else(|_| text.to_owned())
}

fn cc_shrink_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_by(|left, right| left.to_string().cmp(&right.to_string()));
            let mut result = serde_json::Map::new();
            for key in keys.iter().take(30) {
                result.insert((*key).clone(), cc_shrink_json(&map[*key]));
            }
            if keys.len() > 30 {
                result.insert("<omitted_keys>".to_owned(), Value::from(keys.len() - 30));
            }
            Value::Object(result)
        }
        Value::Array(items) if items.len() <= 10 => {
            Value::Array(items.iter().map(cc_shrink_json).collect())
        }
        Value::Array(items) => {
            let mut result = serde_json::Map::new();
            result.insert("length".to_owned(), Value::from(items.len()));
            result.insert(
                "head".to_owned(),
                Value::Array(items.iter().take(6).map(cc_shrink_json).collect()),
            );
            result.insert(
                "tail".to_owned(),
                Value::Array(
                    items
                        .iter()
                        .skip(items.len().saturating_sub(2))
                        .map(cc_shrink_json)
                        .collect(),
                ),
            );
            Value::Object(result)
        }
        Value::String(text) if text.chars().count() > 300 => {
            let length = text.chars().count();
            let head = text.chars().take(180).collect::<String>();
            let tail = text
                .chars()
                .skip(length.saturating_sub(60))
                .collect::<String>();
            Value::String(format!("{head}…<{length} chars>…{tail}"))
        }
        _ => value.clone(),
    }
}

fn cc_restore_python_json_literals(rendered: String) -> Result<String, String> {
    let rendered = rendered
        .replace(&format!("\"{CC_JSON_NAN}\""), "NaN")
        .replace(&format!("\"{CC_JSON_INF}\""), "Infinity")
        .replace(&format!("\"{CC_JSON_NINF}\""), "-Infinity");
    let integer_re = regex::Regex::new(&format!(r#"\"{}(-?[0-9]+)\""#, CC_JSON_INT))
        .map_err(|error| format!("COMPACTOR_BIGINT_REGEX_FAILED:{error}"))?;
    Ok(integer_re.replace_all(&rendered, "$1").into_owned())
}

fn cc_json_or_table_compact(output: &str) -> Result<Option<String>, String> {
    let prepared = cc_compactor_json_compat(output);
    let Ok(value) = serde_json::from_str::<Value>(&prepared) else {
        return Ok(None);
    };
    let shrunk = cc_shrink_json(&value);
    let rendered = serde_json::to_string_pretty(&cc_sort_json(&shrunk))
        .map_err(|error| format!("COMPACTOR_JSON_RENDER_FAILED:{error}"))?;
    cc_restore_python_json_literals(rendered).map(Some)
}

fn cc_compact_compat(command: &str, output: &str) -> Result<Option<(&'static str, String)>, String> {
    if let Some(value) = cc_compact(command, output) {
        return Ok(Some(value));
    }
    let Ok(parts) = cc_shell_split_platform(command) else {
        return Ok(None);
    };
    let Some(index) = cc_executable_index(&parts) else {
        return Ok(None);
    };
    let executable = cc_casefold(cc_basename(&parts[index]));
    let arguments = parts[index + 1..].join(" ").to_lowercase();
    for plugin in COMPACTORS {
        if !plugin.executables.contains(&executable.as_str()) {
            continue;
        }
        if let Some(pattern) = plugin.argument_pattern {
            let matcher = regex::Regex::new(pattern)
                .map_err(|error| format!("COMPACTOR_ROUTE_REGEX_FAILED:{}:{error}", plugin.name))?;
            if !matcher.is_match(&arguments) {
                continue;
            }
        }
        if matches!(plugin.strategy, CompactorStrategy::JsonOrTable) {
            return Ok(cc_json_or_table_compact(output)?.map(|visible| (plugin.name, visible)));
        }
        return Ok(None);
    }
    Ok(None)
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
        if let Some((compactor, visible)) = cc_compact_compat(&command, &output)? {
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
