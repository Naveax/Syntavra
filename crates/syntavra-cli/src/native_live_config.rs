#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

const MAX_CONFIG_FILE_BYTES: u64 = 128 * 1024;
const MAX_CONFIG_WIRE_BYTES: usize = 256 * 1024;
const MAX_OVERRIDE_JSON_BYTES: usize = 64 * 1024;
const ENV_PREFIX: &str = "SYNTAVRA_CFG__";

#[derive(Debug, Clone)]
struct Assignment {
    scope: &'static str,
    source: String,
    path: String,
    value: Value,
}

fn hex_text(value: &str) -> String {
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value.as_bytes() {
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    output
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn decode_lower_hex(value: &str, maximum_bytes: usize, code: &str) -> Result<Vec<u8>, String> {
    if value.is_empty() || value.len() > maximum_bytes.saturating_mul(2) || value.len() % 2 != 0 {
        return Err(code.to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = hex_nibble(pair[0]).ok_or_else(|| code.to_owned())?;
        let low = hex_nibble(pair[1]).ok_or_else(|| code.to_owned())?;
        output.push((high << 4) | low);
    }
    Ok(output)
}

fn scalar_wire(value: &Value) -> Result<(&'static str, String), String> {
    match value {
        Value::Null => Ok(("n", String::new())),
        Value::Bool(flag) => Ok(("b", flag.to_string())),
        Value::Number(number) if number.is_i64() || number.is_u64() => {
            Ok(("i", number.to_string()))
        }
        Value::Number(number) => {
            let parsed = number
                .as_f64()
                .filter(|value| value.is_finite())
                .ok_or_else(|| "LIVE_CONFIG_NON_FINITE".to_owned())?;
            Ok(("f", format!("{parsed:?}")))
        }
        Value::String(text) => Ok(("s", text.clone())),
        Value::Array(_) | Value::Object(_) => Err("LIVE_CONFIG_NON_SCALAR_FORBIDDEN".to_owned()),
    }
}

fn encode_wire(assignments: &[Assignment]) -> Result<Vec<u8>, String> {
    let mut output = String::from("R6CFG1\nphase\t0\n");
    for assignment in assignments {
        let (kind, raw) = scalar_wire(&assignment.value)?;
        writeln!(
            &mut output,
            "a\t{}\t{}\t{}\t{}\t{}",
            assignment.scope,
            hex_text(&assignment.source),
            hex_text(&assignment.path),
            kind,
            hex_text(&raw),
        )
        .expect("writing to a String cannot fail");
    }
    if output.len() > MAX_CONFIG_WIRE_BYTES {
        return Err("LIVE_CONFIG_WIRE_TOO_LARGE".to_owned());
    }
    Ok(output.into_bytes())
}

fn strip_comment(value: &str) -> &str {
    let mut quote = None;
    let mut escaped = false;
    for (index, character) in value.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && quote == Some('"') {
            escaped = true;
            continue;
        }
        if matches!(character, '\'' | '"') {
            if quote == Some(character) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(character);
            }
            continue;
        }
        if character == '#' && quote.is_none() {
            return value[..index].trim_end();
        }
    }
    value.trim_end()
}

fn parse_toml_scalar(value: &str) -> Result<Value, String> {
    let value = strip_comment(value).trim();
    if value.is_empty() {
        return Err("LIVE_CONFIG_VALUE_MISSING".to_owned());
    }
    if value.starts_with('"') {
        return serde_json::from_str::<String>(value)
            .map(Value::String)
            .map_err(|_| "LIVE_CONFIG_STRING_INVALID".to_owned());
    }
    if value.starts_with('\'') {
        return value
            .strip_prefix('\'')
            .and_then(|text| text.strip_suffix('\''))
            .map(|text| Value::String(text.to_owned()))
            .ok_or_else(|| "LIVE_CONFIG_STRING_INVALID".to_owned());
    }
    match value {
        "true" => return Ok(Value::Bool(true)),
        "false" => return Ok(Value::Bool(false)),
        _ => {}
    }
    if value.starts_with('[') || value.starts_with('{') {
        return Err("LIVE_CONFIG_NON_SCALAR_FORBIDDEN".to_owned());
    }
    let normalized = value.replace('_', "");
    if let Ok(integer) = normalized.parse::<i64>() {
        return Ok(Value::Number(integer.into()));
    }
    if let Ok(integer) = normalized.parse::<u64>() {
        return Ok(Value::Number(integer.into()));
    }
    let number = normalized
        .parse::<f64>()
        .map_err(|_| "LIVE_CONFIG_SCALAR_INVALID".to_owned())?;
    if !number.is_finite() {
        return Err("LIVE_CONFIG_NON_FINITE".to_owned());
    }
    serde_json::Number::from_f64(number)
        .map(Value::Number)
        .ok_or_else(|| "LIVE_CONFIG_NON_FINITE".to_owned())
}

fn bare_key(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return Err("LIVE_CONFIG_KEY_INVALID".to_owned());
    }
    Ok(value.to_owned())
}

fn dotted_key(value: &str) -> Result<Vec<String>, String> {
    value.split('.').map(bare_key).collect()
}

fn read_stable_file(path: &Path, scope: &str) -> Result<Option<String>, String> {
    let before = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("LIVE_CONFIG_INSPECT_FAILED:{scope}:{error}")),
    };
    if before.file_type().is_symlink() || !before.is_file() {
        return Err(format!("LIVE_CONFIG_FILE_TYPE_INVALID:{scope}"));
    }
    if before.len() > MAX_CONFIG_FILE_BYTES {
        return Err(format!("LIVE_CONFIG_FILE_TOO_LARGE:{scope}"));
    }
    let payload =
        fs::read(path).map_err(|error| format!("LIVE_CONFIG_READ_FAILED:{scope}:{error}"))?;
    let after = fs::symlink_metadata(path)
        .map_err(|error| format!("LIVE_CONFIG_REINSPECT_FAILED:{scope}:{error}"))?;
    if before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
        || payload.len() != usize::try_from(before.len()).unwrap_or(usize::MAX)
    {
        return Err(format!("LIVE_CONFIG_CHANGED_DURING_READ:{scope}"));
    }
    String::from_utf8(payload)
        .map(Some)
        .map_err(|_| format!("LIVE_CONFIG_UTF8_INVALID:{scope}"))
}

fn parse_toml_layer(
    path: &Path,
    scope: &'static str,
    source: &str,
) -> Result<Vec<Assignment>, String> {
    let Some(text) = read_stable_file(path, scope)? else {
        return Ok(Vec::new());
    };
    let mut section = Vec::<String>::new();
    let mut values = BTreeMap::<String, Value>::new();
    for raw_line in text.lines() {
        let line = strip_comment(raw_line).trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') {
            let inner = line
                .strip_prefix('[')
                .and_then(|item| item.strip_suffix(']'))
                .ok_or_else(|| "LIVE_CONFIG_SECTION_INVALID".to_owned())?;
            if inner.starts_with('[') || inner.ends_with(']') {
                return Err("LIVE_CONFIG_ARRAY_TABLE_FORBIDDEN".to_owned());
            }
            section = dotted_key(inner)?;
            continue;
        }
        let (raw_key, raw_value) = line
            .split_once('=')
            .ok_or_else(|| "LIVE_CONFIG_ASSIGNMENT_INVALID".to_owned())?;
        let mut parts = section.clone();
        parts.extend(dotted_key(raw_key)?);
        let key = parts.join(".");
        if values.insert(key, parse_toml_scalar(raw_value)?).is_some() {
            return Err("LIVE_CONFIG_DUPLICATE_PATH".to_owned());
        }
    }
    Ok(values
        .into_iter()
        .map(|(path, value)| Assignment {
            scope,
            source: source.to_owned(),
            path,
            value,
        })
        .collect())
}

fn environment_scalar(value: &str) -> Result<Value, String> {
    let lowered = value.to_ascii_lowercase();
    if lowered == "true" {
        return Ok(Value::Bool(true));
    }
    if lowered == "false" {
        return Ok(Value::Bool(false));
    }
    if matches!(lowered.as_str(), "null" | "none") {
        return Ok(Value::Null);
    }
    match serde_json::from_str::<Value>(value) {
        Ok(Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_)) => {
            serde_json::from_str(value).map_err(|_| "LIVE_CONFIG_ENV_INVALID".to_owned())
        }
        Ok(Value::Array(_) | Value::Object(_)) => Err("LIVE_CONFIG_ENV_NON_SCALAR".to_owned()),
        Err(_) => Ok(Value::String(value.to_owned())),
    }
}

fn environment_layer() -> Result<Vec<Assignment>, String> {
    let mut rows = env::vars()
        .filter(|(name, _)| name.starts_with(ENV_PREFIX))
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| left.0.cmp(&right.0));
    rows.into_iter()
        .map(|(name, raw)| {
            let path = name[ENV_PREFIX.len()..]
                .to_ascii_lowercase()
                .replace("__", ".");
            if path.is_empty() || path.split('.').any(str::is_empty) {
                return Err("LIVE_CONFIG_ENV_PATH_INVALID".to_owned());
            }
            Ok(Assignment {
                scope: "environment",
                source: name,
                path,
                value: environment_scalar(&raw)?,
            })
        })
        .collect()
}

fn user_config_path() -> Option<PathBuf> {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .map(|home| home.join(".config").join("syntavra").join("config.toml"))
}

fn canonical_json(value: &Value) -> Result<String, String> {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {
            serde_json::to_string(value).map_err(|_| "LIVE_OVERRIDE_JSON_INVALID".to_owned())
        }
        Value::Array(values) => {
            let rows = values
                .iter()
                .map(canonical_json)
                .collect::<Result<Vec<_>, _>>()?;
            Ok(format!("[{}]", rows.join(",")))
        }
        Value::Object(values) => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort();
            let mut rows = Vec::with_capacity(keys.len());
            for key in keys {
                rows.push(format!(
                    "{}:{}",
                    serde_json::to_string(key)
                        .map_err(|_| "LIVE_OVERRIDE_JSON_INVALID".to_owned())?,
                    canonical_json(&values[key])?,
                ));
            }
            Ok(format!("{{{}}}", rows.join(",")))
        }
    }
}

fn flatten_override(
    value: &Value,
    prefix: &str,
    output: &mut BTreeMap<String, Value>,
) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "LIVE_OVERRIDE_OBJECT_REQUIRED".to_owned())?;
    for key in object.keys() {
        if key.is_empty() {
            return Err("LIVE_OVERRIDE_KEY_INVALID".to_owned());
        }
        let path = if prefix.is_empty() {
            key.clone()
        } else {
            format!("{prefix}.{key}")
        };
        match &object[key] {
            Value::Object(_) => flatten_override(&object[key], &path, output)?,
            Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {
                output.insert(path, object[key].clone());
            }
            Value::Array(_) => return Err("LIVE_OVERRIDE_NON_SCALAR_FORBIDDEN".to_owned()),
        }
    }
    Ok(())
}

fn override_layer(
    encoded: Option<&str>,
    scope: &'static str,
    source: &str,
) -> Result<Vec<Assignment>, String> {
    let Some(encoded) = encoded else {
        return Ok(Vec::new());
    };
    let raw = decode_lower_hex(
        encoded,
        MAX_OVERRIDE_JSON_BYTES,
        "LIVE_OVERRIDE_HEX_INVALID",
    )?;
    let value: Value =
        serde_json::from_slice(&raw).map_err(|_| "LIVE_OVERRIDE_JSON_INVALID".to_owned())?;
    if canonical_json(&value)?.as_bytes() != raw {
        return Err("LIVE_OVERRIDE_JSON_NONCANONICAL".to_owned());
    }
    let mut flattened = BTreeMap::new();
    flatten_override(&value, "", &mut flattened)?;
    Ok(flattened
        .into_iter()
        .map(|(path, value)| Assignment {
            scope,
            source: source.to_owned(),
            path,
            value,
        })
        .collect())
}

pub fn discover_wire(
    project_root: &Path,
    session_override_hex: Option<&str>,
    task_override_hex: Option<&str>,
) -> Result<Vec<u8>, String> {
    let root = fs::symlink_metadata(project_root)
        .map_err(|error| format!("LIVE_CONFIG_PROJECT_INSPECT_FAILED:{error}"))?;
    if root.file_type().is_symlink() || !root.is_dir() {
        return Err("LIVE_CONFIG_PROJECT_ROOT_INVALID".to_owned());
    }
    let mut assignments = Vec::new();
    if let Some(path) = user_config_path() {
        assignments.extend(parse_toml_layer(&path, "user", "user-config")?);
    }
    assignments.extend(parse_toml_layer(
        &project_root.join(".syntavra").join("config.toml"),
        "project",
        "project-config",
    )?);
    assignments.extend(environment_layer()?);
    assignments.extend(override_layer(
        session_override_hex,
        "session",
        "session-override",
    )?);
    assignments.extend(override_layer(task_override_hex, "task", "task-override")?);
    encode_wire(&assignments)
}

#[cfg(test)]
mod tests {
    use super::{canonical_json, decode_lower_hex};
    use serde_json::json;

    #[test]
    fn canonical_json_sorts_object_keys() {
        assert_eq!(
            canonical_json(&json!({"b": 2, "a": 1})).expect("canonical"),
            r#"{"a":1,"b":2}"#
        );
    }

    #[test]
    fn override_hex_must_be_lowercase() {
        assert!(decode_lower_hex("7b7d", 64, "INVALID").is_ok());
        assert!(decode_lower_hex("7B7D", 64, "INVALID").is_err());
    }
}
