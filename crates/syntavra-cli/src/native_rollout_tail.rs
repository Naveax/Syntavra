#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const COUNTERS: [&str; 11] = [
    "model_turns",
    "tool_calls",
    "wait_calls",
    "command_calls",
    "compactions",
    "fresh_input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "malformed_lines",
    "duplicate_events",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "rollout-tail")
}

fn option_value<'a>(arguments: &'a [String], flag: &str) -> Result<Option<&'a str>, String> {
    let mut value = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            value = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .as_str(),
            );
        } else if let Some(candidate) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            value = Some(candidate);
        }
        index += 1;
    }
    Ok(value)
}

fn home_directory() -> PathBuf {
    if cfg!(windows) {
        if let Some(value) = env::var_os("USERPROFILE") {
            return PathBuf::from(value);
        }
        if let (Some(drive), Some(path)) = (env::var_os("HOMEDRIVE"), env::var_os("HOMEPATH")) {
            let mut value = PathBuf::from(drive);
            value.push(path);
            return value;
        }
    }
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(unix)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::unix::fs::MetadataExt;
    (metadata.ino(), metadata.dev())
}

#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (
        metadata.file_index().unwrap_or(0),
        u64::from(metadata.volume_serial_number().unwrap_or(0)),
    )
}

#[cfg(not(any(unix, windows)))]
fn file_numbers(_metadata: &fs::Metadata) -> (u64, u64) {
    (0, 0)
}

fn file_identity(path: &Path) -> Result<String, String> {
    let resolved = fs::canonicalize(path)
        .map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?;
    let metadata = fs::metadata(&resolved)
        .map_err(|error| format!("ROLLOUT_METADATA_FAILED:{error}"))?;
    let (inode, device) = file_numbers(&metadata);
    let material = format!("{}|{inode}|{device}", resolved.display());
    Ok(sha256_hex(material.as_bytes()))
}

fn empty_counters() -> BTreeMap<String, i64> {
    COUNTERS
        .iter()
        .map(|name| ((*name).to_owned(), 0))
        .collect()
}

fn integer_like(value: Option<&Value>, default: i64) -> Result<i64, String> {
    let Some(value) = value else {
        return Ok(default);
    };
    if let Some(number) = value.as_i64() {
        return Ok(number);
    }
    if let Some(number) = value.as_u64() {
        return i64::try_from(number).map_err(|_| "ROLLOUT_STATE_INTEGER_INVALID".to_owned());
    }
    if let Some(number) = value.as_f64() {
        if number.is_finite() {
            return Ok(number as i64);
        }
    }
    if let Some(text) = value.as_str() {
        return text
            .parse::<i64>()
            .map_err(|_| "ROLLOUT_STATE_INTEGER_INVALID".to_owned());
    }
    Err("ROLLOUT_STATE_INTEGER_INVALID".to_owned())
}

fn read_state(path: &Path) -> Result<Value, String> {
    if !path.is_file() {
        return Ok(json!({}));
    }
    let bytes = fs::read(path).map_err(|error| format!("ROLLOUT_STATE_READ_FAILED:{error}"))?;
    serde_json::from_slice(&bytes).map_err(|_| "ROLLOUT_STATE_JSON_INVALID".to_owned())
}

fn temp_path(path: &Path) -> Result<PathBuf, String> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("rollout-state.json");
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "ROLLOUT_SYSTEM_CLOCK_INVALID".to_owned())?
        .as_nanos();
    Ok(parent.join(format!(".{name}.{stamp}.tmp")))
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("ROLLOUT_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let temporary = temp_path(path)?;
    let mut bytes = serde_json::to_vec(value)
        .map_err(|_| "ROLLOUT_STATE_JSON_RENDER_FAILED".to_owned())?;
    bytes.push(b'\n');
    let result = (|| {
        let mut handle = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(|error| format!("ROLLOUT_STATE_TEMP_CREATE_FAILED:{error}"))?;
        handle
            .write_all(&bytes)
            .map_err(|error| format!("ROLLOUT_STATE_WRITE_FAILED:{error}"))?;
        handle
            .sync_all()
            .map_err(|error| format!("ROLLOUT_STATE_SYNC_FAILED:{error}"))?;
        drop(handle);
        if path.exists() {
            fs::remove_file(path)
                .map_err(|error| format!("ROLLOUT_STATE_REPLACE_FAILED:{error}"))?;
        }
        fs::rename(&temporary, path)
            .map_err(|error| format!("ROLLOUT_STATE_REPLACE_FAILED:{error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn walk_values<'a>(value: &'a Value, output: &mut Vec<(&'a str, &'a Value)>) {
    match value {
        Value::Object(object) => {
            for (key, item) in object {
                output.push((key.as_str(), item));
                walk_values(item, output);
            }
        }
        Value::Array(items) => {
            for item in items {
                walk_values(item, output);
            }
        }
        _ => {}
    }
}

fn first_number(event: &Value, aliases: &[&str]) -> i64 {
    let alias_set = aliases
        .iter()
        .map(|alias| alias.to_lowercase())
        .collect::<BTreeSet<_>>();
    let mut values = Vec::new();
    walk_values(event, &mut values);
    for (key, value) in values {
        if !alias_set.contains(&key.to_lowercase()) {
            continue;
        }
        if let Some(number) = value.as_i64() {
            if number >= 0 {
                return number;
            }
        } else if let Some(number) = value.as_u64() {
            return i64::try_from(number).unwrap_or(i64::MAX);
        } else if let Some(number) = value.as_f64() {
            if number.is_finite() && number >= 0.0 {
                return number as i64;
            }
        }
    }
    0
}

fn event_id(event: &Map<String, Value>, raw: &[u8]) -> String {
    for key in ["event_id", "id", "call_id", "response_id"] {
        if let Some(value) = event.get(key).and_then(Value::as_str) {
            if !value.is_empty() {
                return format!("{key}:{value}");
            }
        }
    }
    format!("sha256:{}", sha256_hex(raw))
}

fn contains_any(text: &str, tokens: &[&str]) -> bool {
    tokens.iter().any(|token| text.contains(token))
}

fn normalize_event(event: &Map<String, Value>) -> BTreeMap<String, i64> {
    let value = Value::Object(event.clone());
    let text = serde_json::to_string(&value)
        .unwrap_or_default()
        .to_lowercase();
    let event_type = event
        .get("type")
        .or_else(|| event.get("event"))
        .or_else(|| event.get("kind"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_lowercase();
    let mut counters = empty_counters();
    if contains_any(
        &event_type,
        &[
            "response.completed",
            "assistant_message",
            "model_response",
            "turn_context",
        ],
    ) {
        counters.insert("model_turns".to_owned(), 1);
    }
    if event_type.contains("tool") || text.contains("function_call") {
        counters.insert("tool_calls".to_owned(), 1);
    }
    if contains_any(
        &text,
        &["\"wait\"", "write_stdin", "process is still running", "still running"],
    ) {
        counters.insert("wait_calls".to_owned(), 1);
    }
    if contains_any(
        &text,
        &["exec_command", "shell_command", "command_execution"],
    ) {
        counters.insert("command_calls".to_owned(), 1);
    }
    if contains_any(
        &text,
        &["compaction", "compact_context", "context_compacted"],
    ) {
        counters.insert("compactions".to_owned(), 1);
    }
    let total_input = first_number(
        &value,
        &["input_tokens", "input_token_count", "prompt_tokens"],
    );
    let explicit_fresh = first_number(
        &value,
        &["uncached_input_tokens", "fresh_input_tokens"],
    );
    let cached = first_number(
        &value,
        &[
            "cached_input_tokens",
            "cache_read_input_tokens",
            "cached_tokens",
        ],
    );
    counters.insert("cached_input_tokens".to_owned(), cached);
    counters.insert(
        "fresh_input_tokens".to_owned(),
        if explicit_fresh != 0 {
            explicit_fresh
        } else {
            (total_input - cached).max(0)
        },
    );
    counters.insert(
        "output_tokens".to_owned(),
        first_number(
            &value,
            &["output_tokens", "completion_tokens", "output_token_count"],
        ),
    );
    counters.insert(
        "reasoning_tokens".to_owned(),
        first_number(&value, &["reasoning_tokens", "reasoning_token_count"]),
    );
    counters
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("ROLLOUT_PARTIAL_HEX_INVALID".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    let bytes = value.as_bytes();
    for index in (0..bytes.len()).step_by(2) {
        let pair = std::str::from_utf8(&bytes[index..index + 2])
            .map_err(|_| "ROLLOUT_PARTIAL_HEX_INVALID".to_owned())?;
        output.push(
            u8::from_str_radix(pair, 16)
                .map_err(|_| "ROLLOUT_PARTIAL_HEX_INVALID".to_owned())?,
        );
    }
    Ok(output)
}

fn encode_hex(value: &[u8]) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn poll(rollout: &Path, state_file: &Path) -> Result<Value, String> {
    let path = fs::canonicalize(rollout)
        .map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?;
    let identity = file_identity(&path)?;
    let metadata = fs::metadata(&path)
        .map_err(|error| format!("ROLLOUT_METADATA_FAILED:{error}"))?;
    let mut state = read_state(state_file)?;
    let saved_identity = state.get("file_identity").and_then(Value::as_str);
    let saved_offset = integer_like(state.get("offset"), 0)?;
    if saved_identity != Some(identity.as_str())
        || metadata.len() < u64::try_from(saved_offset.max(0)).unwrap_or(u64::MAX)
    {
        state = json!({
            "file_identity": identity,
            "offset": 0,
            "partial_hex": "",
            "counters": empty_counters(),
            "seen": [],
        });
    }

    let mut counters = empty_counters();
    if let Some(saved) = state.get("counters").and_then(Value::as_object) {
        for name in COUNTERS {
            counters.insert(name.to_owned(), integer_like(saved.get(name), 0)?);
        }
    }
    let mut seen_list = state
        .get("seen")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if seen_list.len() > 10_000 {
        seen_list = seen_list.split_off(seen_list.len() - 10_000);
    }
    let mut seen = seen_list.iter().cloned().collect::<BTreeSet<_>>();
    let offset = integer_like(state.get("offset"), 0)?.max(0);
    let partial_hex = state
        .get("partial_hex")
        .and_then(Value::as_str)
        .unwrap_or("");
    let partial = if partial_hex.is_empty() {
        Vec::new()
    } else {
        decode_hex(partial_hex)?
    };

    let mut handle = File::open(&path)
        .map_err(|error| format!("ROLLOUT_OPEN_FAILED:{error}"))?;
    handle
        .seek(SeekFrom::Start(u64::try_from(offset).unwrap_or(0)))
        .map_err(|error| format!("ROLLOUT_SEEK_FAILED:{error}"))?;
    let mut chunk = Vec::new();
    handle
        .read_to_end(&mut chunk)
        .map_err(|error| format!("ROLLOUT_READ_FAILED:{error}"))?;
    let safe_offset = handle
        .stream_position()
        .map_err(|error| format!("ROLLOUT_POSITION_FAILED:{error}"))?;
    let mut buffer = partial;
    buffer.extend(chunk);
    let ends_with_newline = buffer.ends_with(b"\n");
    let mut lines = buffer.split(|byte| *byte == b'\n').collect::<Vec<_>>();
    let trailing = if !buffer.is_empty() && !ends_with_newline {
        lines.pop().unwrap_or_default().to_vec()
    } else {
        if ends_with_newline && lines.last().is_some_and(|line| line.is_empty()) {
            lines.pop();
        }
        Vec::new()
    };

    let mut processed = 0i64;
    for raw in lines {
        if raw.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let event: Value = match serde_json::from_slice(raw) {
            Ok(value) => value,
            Err(_) => {
                *counters.entry("malformed_lines".to_owned()).or_default() += 1;
                continue;
            }
        };
        let Some(object) = event.as_object() else {
            *counters.entry("malformed_lines".to_owned()).or_default() += 1;
            continue;
        };
        let identifier = event_id(object, raw);
        if seen.contains(&identifier) {
            *counters.entry("duplicate_events".to_owned()).or_default() += 1;
            continue;
        }
        seen.insert(identifier.clone());
        seen_list.push(identifier);
        for (key, value) in normalize_event(object) {
            *counters.entry(key).or_default() += value;
        }
        processed += 1;
    }
    if seen_list.len() > 10_000 {
        seen_list = seen_list.split_off(seen_list.len() - 10_000);
    }

    let state_value = json!({
        "file_identity": identity,
        "offset": safe_offset,
        "partial_hex": encode_hex(&trailing),
        "counters": counters,
        "seen": seen_list,
    });
    atomic_write_json(state_file, &state_value)?;
    let fresh = counters.get("fresh_input_tokens").copied().unwrap_or(0);
    let cached = counters.get("cached_input_tokens").copied().unwrap_or(0);
    let waits = counters.get("wait_calls").copied().unwrap_or(0);
    let turns = counters.get("model_turns").copied().unwrap_or(0);
    Ok(json!({
        "processed_events": processed,
        "offset": safe_offset,
        "partial_bytes": trailing.len(),
        "counters": counters,
        "efficiency": {
            "fresh_fraction": fresh as f64 / (fresh + cached).max(1) as f64,
            "wait_calls_per_turn": waits as f64 / turns.max(1) as f64,
        },
    }))
}

fn discover_recursive(root: &Path, output: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = fs::read_dir(root)
        .map_err(|error| format!("ROLLOUT_DISCOVERY_READ_FAILED:{error}"))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("ROLLOUT_DISCOVERY_ENTRY_FAILED:{error}"))?;
        let file_type = entry
            .file_type()
            .map_err(|error| format!("ROLLOUT_DISCOVERY_TYPE_FAILED:{error}"))?;
        let path = entry.path();
        if file_type.is_dir() {
            discover_recursive(&path, output)?;
        } else if file_type.is_file()
            && path.extension().and_then(|value| value.to_str()) == Some("jsonl")
        {
            output.push(path);
        }
    }
    Ok(())
}

fn discover_rollouts(root: &Path) -> Result<Vec<PathBuf>, String> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut candidates = Vec::new();
    discover_recursive(root, &mut candidates)?;
    candidates.sort_by(|left, right| {
        let left_time = fs::metadata(left)
            .and_then(|metadata| metadata.modified())
            .unwrap_or(UNIX_EPOCH);
        let right_time = fs::metadata(right)
            .and_then(|metadata| metadata.modified())
            .unwrap_or(UNIX_EPOCH);
        right_time.cmp(&left_time)
    });
    Ok(candidates)
}

fn select_active(candidates: &[PathBuf], session_hint: Option<&str>) -> Option<PathBuf> {
    if let Some(hint) = session_hint {
        for path in candidates {
            let name_matches = path
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|name| name.contains(hint));
            let parent_matches = path
                .parent()
                .is_some_and(|parent| parent.to_string_lossy().contains(hint));
            if name_matches || parent_matches {
                return Some(path.clone());
            }
        }
    }
    candidates.first().cloned()
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let explicit = option_value(arguments, "--rollout")?;
    let codex_home = option_value(arguments, "--codex-home")?
        .map(PathBuf::from)
        .or_else(|| env::var_os("CODEX_HOME").map(PathBuf::from))
        .unwrap_or_else(|| home_directory().join(".codex"));
    let selected = if let Some(path) = explicit {
        Some(
            fs::canonicalize(path)
                .map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?,
        )
    } else {
        select_active(
            &discover_rollouts(&codex_home)?,
            option_value(arguments, "--session-hint")?,
        )
    };
    let Some(selected) = selected else {
        return Ok(json!({"ok": false, "reason": "no rollout found"}));
    };
    let state_file = option_value(arguments, "--state-file")?
        .map(PathBuf::from)
        .unwrap_or_else(|| state_root.join("rollout-state.json"));
    let result = poll(&selected, &state_file)?;
    let mut object = result
        .as_object()
        .cloned()
        .ok_or_else(|| "ROLLOUT_RESULT_OBJECT_INVALID".to_owned())?;
    object.insert(
        "rollout".to_owned(),
        Value::String(selected.to_string_lossy().into_owned()),
    );
    Ok(Value::Object(object))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn rollout_tail_is_supported() {
        assert!(supports(&["rollout-tail".to_owned()]));
    }
}
