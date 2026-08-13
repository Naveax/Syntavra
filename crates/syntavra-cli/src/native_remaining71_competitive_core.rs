#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use regex::Regex;
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

include!("native_remaining71_competitive_core_rewrite.inc");
include!("native_remaining71_competitive_core_compactors.inc");
include!("native_remaining71_competitive_core_runtime.inc");

pub(crate) fn supports(command: &[String]) -> bool {
    std::env::var_os("SYNTAVRA_BULK_PARITY_PROBE").is_some_and(|value| value == "1")
        && matches!(command, [root, action] if root == "run" && matches!(action.as_str(), "rewrite" | "transcript-mine"))
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    match command[1].as_str() {
        "rewrite" => rewrite_action(arguments).map(Some),
        "transcript-mine" => transcript_mine(arguments).map(Some),
        _ => Ok(None),
    }
}

fn rewrite_action(arguments: &[String]) -> Result<Value, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "rewrite")
        .map(|index| index + 2)
        .ok_or_else(|| "REWRITE_ACTION_NOT_FOUND".to_owned())?;
    let argv = arguments[start..]
        .iter()
        .filter(|value| value.as_str() != "--")
        .cloned()
        .collect::<Vec<_>>();
    if argv.is_empty() {
        return Err("rewrite command is required".to_owned());
    }
    full_rewrite_argv(&argv)
}

fn transcript_mine(arguments: &[String]) -> Result<Value, String> {
    let source = positional_after(arguments, "transcript-mine", 0, &[])?;
    let text = if Path::new(source).is_file() {
        fs::read_to_string(source).map_err(|error| format!("TRANSCRIPT_READ_FAILED:{error}"))?
    } else {
        source.to_owned()
    };
    let events = load_transcript(&text);
    let mut opportunities = Vec::<Value>::new();
    let mut commands = 0usize;

    for (index, row) in events.iter().enumerate() {
        let command = transcript_command(row);
        if command.is_empty() {
            continue;
        }
        commands += 1;
        let output = transcript_output(row);
        let input_tokens = ((command.as_bytes().len() + output.as_bytes().len()) / 4).max(1);

        let rewrite = full_rewrite_text(&command)?;
        if rewrite["changed"].as_bool().unwrap_or(false) {
            let saved = (output.chars().count() / 20).max(8);
            opportunities.push(json!({
                "index": index,
                "command": command,
                "kind": "pre-tool-rewrite",
                "estimated_input_tokens": input_tokens,
                "estimated_saved_tokens": saved,
                "recommendation": "rewrite before execution",
                "rule": rewrite["rule"],
                "compactor": Value::Null,
            }));
        }

        if let Some((compactor, visible)) = full_compact_output(&command, &output)? {
            let saved = output
                .as_bytes()
                .len()
                .saturating_sub(visible.as_bytes().len())
                / 4;
            if saved > 0 {
                opportunities.push(json!({
                    "index": index,
                    "command": command,
                    "kind": "post-tool-compaction",
                    "estimated_input_tokens": input_tokens,
                    "estimated_saved_tokens": saved,
                    "recommendation": "capture exact output and return compact view",
                    "rule": Value::Null,
                    "compactor": compactor,
                }));
            }
        }
    }

    let total = opportunities
        .iter()
        .map(|row| row["estimated_saved_tokens"].as_u64().unwrap_or(0))
        .sum::<u64>();
    let mut body = json!({
        "events": events.len(),
        "commands": commands,
        "opportunities": opportunities,
        "estimated_saved_tokens": total,
        "coverage": {
            "rewrite_rules": REWRITE_RULE_COUNT,
            "compactors": COMPACTOR_COUNT,
        },
    });
    body["analysis_hash"] = Value::String(sha256_hex(canonical_json(&body)?.as_bytes()));
    Ok(body)
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn load_transcript(text: &str) -> Vec<Value> {
    if let Ok(value) = serde_json::from_str::<Value>(text) {
        if let Some(rows) = value.as_array() {
            return rows.iter().filter(|row| row.is_object()).cloned().collect();
        }
        if let Some(object) = value.as_object() {
            let selected = ["events", "messages", "transcript"]
                .iter()
                .filter_map(|key| object.get(*key))
                .find(|candidate| json_truthy(candidate));
            if let Some(rows) = selected.and_then(Value::as_array) {
                return rows.iter().filter(|row| row.is_object()).cloned().collect();
            }
            return Vec::new();
        }
        return Vec::new();
    }

    python_splitlines(text)
        .into_iter()
        .filter_map(|line| serde_json::from_str::<Value>(&line).ok())
        .filter(Value::is_object)
        .collect()
}

fn transcript_command(row: &Value) -> String {
    for key in ["command", "cmd", "shell_command", "input"] {
        if let Some(value) = row.get(key).and_then(Value::as_str) {
            return value.to_owned();
        }
    }
    let nested = row.get("tool_input").filter(|value| json_truthy(value))
        .or_else(|| row.get("arguments"));
    if let Some(object) = nested.and_then(Value::as_object) {
        for key in ["command", "cmd"] {
            if let Some(value) = object.get(key).and_then(Value::as_str) {
                return value.to_owned();
            }
        }
    }
    String::new()
}

fn transcript_output(row: &Value) -> String {
    for key in ["output", "stdout", "result", "content"] {
        if let Some(value) = row.get(key).and_then(Value::as_str) {
            return value.to_owned();
        }
    }
    String::new()
}

fn positionals_after<'a>(
    arguments: &'a [String],
    action: &str,
    value_flags: &[&str],
) -> Result<Vec<&'a str>, String> {
    let mut index = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("ACTION_NOT_FOUND:{action}"))?;
    let mut values = Vec::new();
    while index < arguments.len() {
        if value_flags.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        values.push(arguments[index].as_str());
        index += 1;
    }
    Ok(values)
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    position: usize,
    value_flags: &[&str],
) -> Result<&'a str, String> {
    positionals_after(arguments, action, value_flags)?
        .get(position)
        .copied()
        .ok_or_else(|| format!("POSITIONAL_MISSING:{action}:{position}"))
}

fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(value))
        .map_err(|error| format!("JSON_SERIALIZE_FAILED:{error}"))
}

fn sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sort_json(&map[key]));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(sort_json).collect()),
        _ => value.clone(),
    }
}
