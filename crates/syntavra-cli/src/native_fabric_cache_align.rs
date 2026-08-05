#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, Read as _};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const VOLATILE_FIELDS: &[&str] = &[
    "cost",
    "created_at",
    "duration_ms",
    "latency_ms",
    "request_id",
    "response_id",
    "span_id",
    "timestamp",
    "trace_id",
    "updated_at",
    "usage",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "cache-align")
}

#[derive(Debug)]
struct Options {
    input: Option<String>,
    output: Option<PathBuf>,
    payload: String,
    keep_tail: i64,
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "cache-align")
        .map(|index| index + 2)
        .ok_or_else(|| "FABRIC_CACHE_ALIGN_COMMAND_MISSING".to_owned())
}

fn take_value(arguments: &[String], index: &mut usize, name: &str) -> Result<String, String> {
    *index += 1;
    arguments
        .get(*index)
        .cloned()
        .ok_or_else(|| format!("{name}_VALUE_MISSING"))
}

fn parse_options(arguments: &[String]) -> Result<Options, String> {
    let mut input = None;
    let mut output = None;
    let mut payload = String::new();
    let mut keep_tail = 1_i64;
    let mut index = command_start(arguments)?;
    while index < arguments.len() {
        let item = &arguments[index];
        match item.as_str() {
            "--input" => input = Some(take_value(arguments, &mut index, "--input")?),
            "--output" => output = Some(PathBuf::from(take_value(arguments, &mut index, "--output")?)),
            "--payload" => payload = take_value(arguments, &mut index, "--payload")?,
            "--keep-tail" => {
                let raw = take_value(arguments, &mut index, "--keep-tail")?;
                keep_tail = raw
                    .parse::<i64>()
                    .map_err(|error| format!("--keep-tail_INVALID:{error}"))?;
            }
            _ => {
                if let Some(value) = item.strip_prefix("--input=") {
                    input = Some(value.to_owned());
                } else if let Some(value) = item.strip_prefix("--output=") {
                    output = Some(PathBuf::from(value));
                } else if let Some(value) = item.strip_prefix("--payload=") {
                    payload = value.to_owned();
                } else if let Some(value) = item.strip_prefix("--keep-tail=") {
                    keep_tail = value
                        .parse::<i64>()
                        .map_err(|error| format!("--keep-tail_INVALID:{error}"))?;
                } else {
                    return Err(format!("FABRIC_CACHE_ALIGN_ARGUMENT_UNSUPPORTED:{item}"));
                }
            }
        }
        index += 1;
    }
    Ok(Options {
        input,
        output,
        payload,
        keep_tail,
    })
}

fn option_value(arguments: &[String], name: &str) -> Option<String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            if let Some(value) = arguments.get(index + 1) {
                found = Some(value.clone());
                index += 1;
            }
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    found
}

fn read_payload(options: &Options) -> Result<Value, String> {
    let raw = match options.input.as_deref() {
        Some("-") => {
            let mut value = String::new();
            io::stdin()
                .read_to_string(&mut value)
                .map_err(|error| format!("FABRIC_CACHE_ALIGN_STDIN_READ_FAILED:{error}"))?;
            value
        }
        Some(path) => fs::read_to_string(path)
            .map_err(|error| format!("FABRIC_CACHE_ALIGN_INPUT_READ_FAILED:{error}"))?,
        None => options.payload.clone(),
    };
    if raw.trim().is_empty() {
        return Ok(Value::Object(Map::new()));
    }
    serde_json::from_str(&raw)
        .map_err(|error| format!("FABRIC_CACHE_ALIGN_JSON_INVALID:{error}"))
}

fn volatile(name: &str) -> bool {
    name.starts_with('_') || VOLATILE_FIELDS.contains(&name)
}

fn stable_copy(value: &Value, volatile_fields: &mut BTreeSet<String>) -> Value {
    match value {
        Value::Object(values) => {
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            let mut result = Map::new();
            for (key, value) in ordered {
                if volatile(key) {
                    volatile_fields.insert(key.clone());
                    continue;
                }
                result.insert(key.clone(), stable_copy(value, volatile_fields));
            }
            Value::Object(result)
        }
        Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|value| stable_copy(value, volatile_fields))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn canonical_into(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(
            &serde_json::to_string(value)
                .map_err(|error| format!("FABRIC_CACHE_ALIGN_STRING_FAILED:{error}"))?,
        ),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                canonical_into(value, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            for (index, (key, value)) in ordered.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(
                    &serde_json::to_string(key)
                        .map_err(|error| format!("FABRIC_CACHE_ALIGN_KEY_FAILED:{error}"))?,
                );
                output.push(':');
                canonical_into(value, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

fn canonical(value: &Value) -> Result<String, String> {
    let mut output = String::new();
    canonical_into(value, &mut output)?;
    Ok(output)
}

fn align(payload: Value, keep_tail: i64) -> Result<Value, String> {
    if keep_tail < 0 {
        return Err("keep_tail must be non-negative".to_owned());
    }
    let messages = match payload {
        Value::Object(mut values) => values.remove("messages").unwrap_or(Value::Null),
        value => value,
    };
    let values = messages
        .as_array()
        .ok_or_else(|| "input must be a message list or an object containing messages".to_owned())?;
    let stable_message_count = values.len().saturating_sub(keep_tail as usize);
    let mut volatile_fields = BTreeSet::new();
    let stable = Value::Array(
        values[..stable_message_count]
            .iter()
            .map(|value| stable_copy(value, &mut volatile_fields))
            .collect(),
    );
    let canonical_prefix = canonical(&stable)?;
    let cacheable_bytes = canonical_prefix.len();
    Ok(json!({
        "prefix_hash": sha256_hex(canonical_prefix.as_bytes()),
        "stable_message_count": stable_message_count,
        "volatile_tail_count": values.len() - stable_message_count,
        "cacheable_bytes": cacheable_bytes,
        "volatile_fields": volatile_fields.into_iter().collect::<Vec<_>>(),
        "canonical_prefix": canonical_prefix,
    }))
}

fn ensure_schema(state_root: &Path) -> Result<Connection, String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("FABRIC_STATE_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(state_root.join("competitive-fabric.sqlite3"))
        .map_err(|error| format!("FABRIC_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS fabric_events(\
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,\
                event_type TEXT NOT NULL,\
                family TEXT NOT NULL,\
                host TEXT NOT NULL,\
                raw_bytes INTEGER NOT NULL,\
                visible_bytes INTEGER NOT NULL,\
                latency_ms REAL NOT NULL,\
                success INTEGER NOT NULL,\
                cache_hit INTEGER NOT NULL,\
                metadata_json TEXT NOT NULL,\
                created_at REAL NOT NULL\
            );\
            CREATE INDEX IF NOT EXISTS fabric_event_type_idx \
                ON fabric_events(event_type,created_at);\
            CREATE INDEX IF NOT EXISTS fabric_family_idx \
                ON fabric_events(family,created_at);",
        )
        .map_err(|error| format!("FABRIC_DATABASE_SCHEMA_FAILED:{error}"))?;
    Ok(connection)
}

fn record_event(
    connection: &Connection,
    host: &str,
    value: &Value,
    latency_ms: f64,
) -> Result<(), String> {
    let cacheable_bytes = value["cacheable_bytes"].as_u64().unwrap_or(0) as i64;
    let stable_messages = value["stable_message_count"].as_u64().unwrap_or(0);
    let metadata = format!("{{\"stable_messages\": {stable_messages}}}");
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("FABRIC_CLOCK_FAILED:{error}"))?
        .as_secs_f64();
    connection
        .execute(
            "INSERT INTO fabric_events(\
                event_type,family,host,raw_bytes,visible_bytes,latency_ms,\
                success,cache_hit,metadata_json,created_at\
            ) VALUES(?,?,?,?,?,?,?,?,?,?)",
            params![
                "cache-align",
                "provider-request",
                host,
                cacheable_bytes,
                cacheable_bytes,
                latency_ms.max(0.0),
                1_i64,
                0_i64,
                metadata,
                created_at,
            ],
        )
        .map_err(|error| format!("FABRIC_EVENT_INSERT_FAILED:{error}"))?;
    Ok(())
}

fn write_output(path: &Path, value: &Value) -> Result<Value, String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_OUTPUT_PARENT_FAILED:{error}"))?;
        }
    }
    let rendered = serde_json::to_string_pretty(value)
        .map_err(|error| format!("FABRIC_OUTPUT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(path, rendered.as_bytes())
        .map_err(|error| format!("FABRIC_OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "output": path.to_string_lossy(),
        "bytes": rendered.len(),
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let options = parse_options(arguments)?;
    let connection = ensure_schema(state_root)?;
    let payload = read_payload(&options)?;
    let host = option_value(arguments, "--host").unwrap_or_else(|| "codex".to_owned());
    let started = Instant::now();
    let value = align(payload, options.keep_tail)?;
    record_event(
        &connection,
        &host,
        &value,
        started.elapsed().as_secs_f64() * 1000.0,
    )?;
    options
        .output
        .as_deref()
        .map_or_else(|| Ok(value.clone()), |path| write_output(path, &value))
}

#[cfg(test)]
mod tests {
    use super::align;
    use serde_json::json;

    #[test]
    fn removes_recursive_volatile_fields() {
        let value = align(
            json!({
                "messages": [
                    {
                        "role": "system",
                        "content": "stable",
                        "request_id": "volatile",
                        "nested": {"timestamp": 1, "keep": true},
                    },
                    {"role": "user", "content": "tail"},
                ]
            }),
            1,
        )
        .unwrap();
        assert_eq!(value["stable_message_count"], 1);
        assert_eq!(value["volatile_fields"], json!(["request_id", "timestamp"]));
        assert_eq!(
            value["canonical_prefix"],
            "[{\"content\":\"stable\",\"nested\":{\"keep\":true},\"role\":\"system\"}]"
        );
    }

    #[test]
    fn rejects_negative_tail() {
        assert_eq!(
            align(json!({"messages": []}), -1).unwrap_err(),
            "keep_tail must be non-negative"
        );
    }
}
