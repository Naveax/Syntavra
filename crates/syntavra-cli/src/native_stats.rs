#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use serde_json::{json, Value};

const MAX_ANALYTICS_BYTES: u64 = 64 * 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "stats")
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

fn python_int(value: Option<&Value>) -> Result<i64, String> {
    match value {
        None => Ok(0),
        Some(Value::Bool(value)) => Ok(i64::from(*value)),
        Some(Value::Number(value)) => value
            .as_i64()
            .or_else(|| value.as_u64().and_then(|number| i64::try_from(number).ok()))
            .or_else(|| value.as_f64().map(|number| number.trunc() as i64))
            .ok_or_else(|| "ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(Value::String(value)) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| "ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(_) => Err("ANALYTICS_INTEGER_INVALID".to_owned()),
    }
}

fn python_float(value: Option<&Value>) -> Result<f64, String> {
    let number = match value {
        None => 0.0,
        Some(Value::Bool(value)) => f64::from(u8::from(*value)),
        Some(Value::Number(value)) => value
            .as_f64()
            .ok_or_else(|| "ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(Value::String(value)) => value
            .trim()
            .parse::<f64>()
            .map_err(|_| "ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(_) => return Err("ANALYTICS_FLOAT_INVALID".to_owned()),
    };
    if number.is_finite() {
        Ok(number)
    } else {
        Err("ANALYTICS_FLOAT_NONFINITE".to_owned())
    }
}

fn identity_string(value: &Value) -> Option<String> {
    if !json_truthy(value) {
        return None;
    }
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Bool(value) => Some(if *value { "True" } else { "False" }.to_owned()),
        Value::Number(value) => Some(value.to_string()),
        Value::Null => None,
        Value::Array(_) | Value::Object(_) => serde_json::to_string(value).ok(),
    }
}

fn rows(path: &Path) -> Result<Vec<Value>, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("ANALYTICS_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("ANALYTICS_READ_FAILED:{error}")),
    };
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_ANALYTICS_BYTES {
        return Err("ANALYTICS_FILE_TOO_LARGE".to_owned());
    }
    let text = String::from_utf8(bytes).map_err(|_| "ANALYTICS_UTF8_INVALID".to_owned())?;
    let mut output = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value =
            serde_json::from_str(line).map_err(|_| "ANALYTICS_JSONL_INVALID".to_owned())?;
        if value.is_object() {
            output.push(value);
        }
    }
    Ok(output)
}

pub fn execute(state_root: &Path) -> Result<Value, String> {
    let rows = rows(&state_root.join("analytics").join("events.jsonl"))?;
    let mut sessions = BTreeSet::new();
    let mut repositories = BTreeSet::new();
    let mut input_tokens = 0_i64;
    let mut cached_tokens = 0_i64;
    let mut output_tokens = 0_i64;
    let mut wall_time_ms = 0.0_f64;
    let mut cost_usd = 0.0_f64;
    let mut compaction_ms = 0.0_f64;
    let mut continuity = 0_u64;
    let mut route_denied = 0_u64;

    for row in &rows {
        let object = row
            .as_object()
            .ok_or_else(|| "ANALYTICS_ROW_INVALID".to_owned())?;
        if let Some(value) = object.get("session_id").and_then(identity_string) {
            sessions.insert(value);
        }
        if let Some(value) = object.get("repository_hash").and_then(identity_string) {
            repositories.insert(value);
        }
        input_tokens = input_tokens.saturating_add(python_int(object.get("input_tokens"))?.max(0));
        cached_tokens =
            cached_tokens.saturating_add(python_int(object.get("cached_input_tokens"))?.max(0));
        output_tokens =
            output_tokens.saturating_add(python_int(object.get("output_tokens"))?.max(0));
        wall_time_ms += python_float(object.get("wall_time_ms"))?.max(0.0);
        cost_usd += python_float(object.get("cost_usd"))?.max(0.0);
        compaction_ms += python_float(object.get("compaction_ms"))?.max(0.0);
        continuity += u64::from(object.get("continuity_restored").is_some_and(json_truthy));
        route_denied += u64::from(matches!(
            object.get("tool_route_allowed"),
            Some(Value::Bool(false))
        ));
    }

    Ok(json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "events": rows.len(),
        "sessions": sessions.len(),
        "repositories": repositories.len(),
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "billable_input_tokens": input_tokens.saturating_sub(cached_tokens).max(0),
            "output_tokens": output_tokens,
            "wall_time_ms": wall_time_ms,
            "cost_usd": cost_usd,
        },
        "continuity": {
            "restores": continuity,
            "compaction_wall_time_ms": compaction_ms,
        },
        "routing": {"denied": route_denied},
        "privacy": "content-free local aggregate",
    }))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn stats_command_is_supported() {
        assert!(supports(&["stats".to_owned()]));
    }
}
