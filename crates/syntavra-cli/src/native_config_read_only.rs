#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::config_contract::{
    explain_config_wire_json, resolve_config_wire, snapshot_json, ConfigScalar,
};

const MAX_CONFIG_FILE_BYTES: u64 = 128 * 1024;
const MAX_CONFIG_WIRE_BYTES: usize = 256 * 1024;
const ENV_PREFIX: &str = "SYNTAVRA_CFG__";

#[derive(Clone)]
struct Assignment {
    scope: &'static str,
    source: String,
    path: String,
    value: ConfigScalar,
}

fn hex(value: &str) -> String {
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value.as_bytes() {
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    output
}

fn scalar_wire(value: &ConfigScalar) -> (&'static str, String) {
    match value {
        ConfigScalar::Null => ("n", String::new()),
        ConfigScalar::Bool(flag) => ("b", flag.to_string()),
        ConfigScalar::Number(number) => {
            let kind = if number.contains('.') || number.contains('e') || number.contains('E') {
                "f"
            } else {
                "i"
            };
            (kind, number.clone())
        }
        ConfigScalar::String(text) => ("s", text.clone()),
    }
}

fn encode_wire(assignments: &[Assignment]) -> Result<Vec<u8>, String> {
    let mut output = String::from("R6CFG1\nphase\t0\n");
    for assignment in assignments {
        let (kind, raw) = scalar_wire(&assignment.value);
        writeln!(
            &mut output,
            "a\t{}\t{}\t{}\t{}\t{}",
            assignment.scope,
            hex(&assignment.source),
            hex(&assignment.path),
            kind,
            hex(&raw),
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

fn parse_basic_string(value: &str) -> Result<String, String> {
    serde_json::from_str::<String>(value).map_err(|_| "LIVE_CONFIG_STRING_INVALID".to_owned())
}

fn parse_literal_string(value: &str) -> Result<String, String> {
    value
        .strip_prefix('\'')
        .and_then(|text| text.strip_suffix('\''))
        .map(str::to_owned)
        .ok_or_else(|| "LIVE_CONFIG_STRING_INVALID".to_owned())
}

fn parse_toml_scalar(value: &str) -> Result<ConfigScalar, String> {
    let value = strip_comment(value).trim();
    if value.is_empty() {
        return Err("LIVE_CONFIG_VALUE_MISSING".to_owned());
    }
    if value.starts_with('"') {
        return parse_basic_string(value).map(ConfigScalar::String);
    }
    if value.starts_with('\'') {
        return parse_literal_string(value).map(ConfigScalar::String);
    }
    match value {
        "true" => return Ok(ConfigScalar::Bool(true)),
        "false" => return Ok(ConfigScalar::Bool(false)),
        _ => {}
    }
    if value.starts_with('[') || value.starts_with('{') {
        return Err("LIVE_CONFIG_NON_SCALAR_FORBIDDEN".to_owned());
    }
    let normalized = value.replace('_', "");
    if normalized.parse::<i128>().is_ok() {
        return Ok(ConfigScalar::Number(normalized));
    }
    let number = normalized
        .parse::<f64>()
        .map_err(|_| "LIVE_CONFIG_SCALAR_INVALID".to_owned())?;
    if !number.is_finite() {
        return Err("LIVE_CONFIG_NON_FINITE".to_owned());
    }
    let rendered = if normalized.contains('.') {
        normalized
    } else {
        format!("{number:?}")
    };
    Ok(ConfigScalar::Number(rendered))
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
    let payload = fs::read(path).map_err(|error| format!("LIVE_CONFIG_READ_FAILED:{scope}:{error}"))?;
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
    let mut values = BTreeMap::<String, ConfigScalar>::new();
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
        let path = parts.join(".");
        if values.insert(path, parse_toml_scalar(raw_value)?).is_some() {
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

fn environment_scalar(value: &str) -> Result<ConfigScalar, String> {
    let lowered = value.to_ascii_lowercase();
    if lowered == "true" {
        return Ok(ConfigScalar::Bool(true));
    }
    if lowered == "false" {
        return Ok(ConfigScalar::Bool(false));
    }
    if matches!(lowered.as_str(), "null" | "none") {
        return Ok(ConfigScalar::Null);
    }
    match serde_json::from_str::<Value>(value) {
        Ok(Value::Null) => Ok(ConfigScalar::Null),
        Ok(Value::Bool(flag)) => Ok(ConfigScalar::Bool(flag)),
        Ok(Value::Number(number)) => Ok(ConfigScalar::Number(number.to_string())),
        Ok(Value::String(text)) => Ok(ConfigScalar::String(text)),
        Ok(Value::Array(_) | Value::Object(_)) => Err("LIVE_CONFIG_ENV_NON_SCALAR".to_owned()),
        Err(_) => Ok(ConfigScalar::String(value.to_owned())),
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

fn discover_wire(project_root: &Path) -> Result<Vec<u8>, String> {
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
    encode_wire(&assignments)
}

fn command_path(arguments: &[String]) -> Result<String, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "config" && window[1] == "explain")
        .ok_or_else(|| "CONFIG_EXPLAIN_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 2)
        .filter(|value| !value.starts_with('-'))
        .cloned()
        .ok_or_else(|| "CONFIG_EXPLAIN_PATH_MISSING".to_owned())
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [group, action]
        if group == "config" && matches!(action.as_str(), "explain" | "show" | "validate"))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
) -> Result<Value, String> {
    let wire = discover_wire(project_root)?;
    match command {
        [group, action] if group == "config" && action == "show" => {
            let snapshot = resolve_config_wire(&wire)?;
            serde_json::from_str(&snapshot_json(&snapshot)?)
                .map_err(|_| "CONFIG_SHOW_JSON_INVALID".to_owned())
        }
        [group, action] if group == "config" && action == "explain" => {
            let path = command_path(arguments)?;
            serde_json::from_str(&explain_config_wire_json(&wire, path.as_bytes())?)
                .map_err(|_| "CONFIG_EXPLAIN_JSON_INVALID".to_owned())
        }
        [group, action] if group == "config" && action == "validate" => {
            let snapshot = resolve_config_wire(&wire)?;
            Ok(json!({
                "ok": true,
                "config_hash": snapshot.config_hash,
                "warnings": snapshot.warnings,
            }))
        }
        _ => Err("RUST_CONFIG_READ_ONLY_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{environment_scalar, parse_toml_scalar, ConfigScalar};

    #[test]
    fn parses_bounded_toml_scalars() {
        assert_eq!(
            parse_toml_scalar("\"compact\"").expect("string"),
            ConfigScalar::String("compact".to_owned())
        );
        assert_eq!(
            parse_toml_scalar("4_096").expect("integer"),
            ConfigScalar::Number("4096".to_owned())
        );
    }

    #[test]
    fn parses_environment_values_like_python() {
        assert_eq!(
            environment_scalar("true").expect("boolean"),
            ConfigScalar::Bool(true)
        );
        assert_eq!(
            environment_scalar("secret://provider/key").expect("string"),
            ConfigScalar::String("secret://provider/key".to_owned())
        );
    }
}
