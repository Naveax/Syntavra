#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

const CATALOG: &str =
    include_str!("../../../contracts/engine/r38-native-adapter-catalog-v1.json");
const CLAIM_BOUNDARY: &str =
    "Certified requires a live-host external execution receipt";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "adapter-configure")
}

fn parse_arguments(arguments: &[String]) -> Result<(String, String, String, bool), String> {
    let start = arguments
        .windows(2)
        .position(|window| window[0] == "run" && window[1] == "adapter-configure")
        .ok_or_else(|| "ADAPTER_CONFIGURE_COMMAND_MISSING".to_owned())?;
    let mut apply = false;
    let mut positionals = Vec::new();
    for argument in &arguments[start + 2..] {
        if argument == "--apply" {
            apply = true;
        } else if argument.starts_with("--") {
            return Err(format!("ADAPTER_CONFIGURE_OPTION_UNKNOWN:{argument}"));
        } else {
            positionals.push(argument.clone());
        }
    }
    if positionals.len() != 3 {
        return Err(format!(
            "ADAPTER_CONFIGURE_ARGUMENT_COUNT_INVALID:{}",
            positionals.len()
        ));
    }
    Ok((
        positionals.remove(0),
        positionals.remove(0),
        positionals.remove(0),
        apply,
    ))
}

fn home() -> PathBuf {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn expanded_path(candidate: &str, project_root: &Path, home_root: &Path) -> PathBuf {
    let expanded = if let Some(suffix) = candidate.strip_prefix('~') {
        PathBuf::from(format!("{}{}", home_root.to_string_lossy(), suffix))
    } else {
        PathBuf::from(candidate)
    };
    if expanded.is_absolute() {
        expanded
    } else {
        project_root.join(expanded)
    }
}

fn catalog() -> Result<Value, String> {
    serde_json::from_str(CATALOG)
        .map_err(|error| format!("ADAPTER_CATALOG_INVALID:{error}"))
}

fn adapter_record<'a>(catalog: &'a Value, adapter_id: &str) -> Result<&'a Value, String> {
    let records = catalog["records"]
        .as_array()
        .ok_or_else(|| "ADAPTER_CATALOG_RECORDS_INVALID".to_owned())?;
    let mut matches = records
        .iter()
        .filter(|record| record["adapter_id"].as_str() == Some(adapter_id));
    let record = matches
        .next()
        .ok_or_else(|| format!("ADAPTER_NOT_FOUND:{adapter_id}"))?;
    if matches.next().is_some() {
        return Err(format!("ADAPTER_DUPLICATE:{adapter_id}"));
    }
    Ok(record)
}

fn load_json_object(argument: &str) -> Result<Map<String, Value>, String> {
    let path = Path::new(argument);
    let source = if path.is_file() {
        fs::read_to_string(path)
            .map_err(|error| format!("ADAPTER_DESIRED_READ_FAILED:{error}"))?
    } else {
        argument.to_owned()
    };
    let value = serde_json::from_str::<Value>(&source)
        .map_err(|error| format!("ADAPTER_DESIRED_JSON_INVALID:{error}"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| "ADAPTER_DESIRED_MUST_BE_OBJECT".to_owned())
}

fn merge_json(current: &Value, desired: &Map<String, Value>) -> Value {
    let mut merged = current.as_object().cloned().unwrap_or_default();
    for (key, desired_value) in desired {
        let value = match (merged.get(key), desired_value.as_object()) {
            (Some(current_value), Some(desired_object)) if current_value.is_object() => {
                merge_json(current_value, desired_object)
            }
            _ => desired_value.clone(),
        };
        merged.insert(key.clone(), value);
    }
    Value::Object(merged)
}

fn escape_json_string(value: &str, ensure_ascii: bool) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{0008}' => output.push_str("\\b"),
            '\u{000c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            item if u32::from(item) <= 0x1f => {
                output.push_str(&format!("\\u{:04x}", u32::from(item)));
            }
            item if ensure_ascii && u32::from(item) > 0x7f => {
                let scalar = u32::from(item);
                if scalar <= 0xffff {
                    output.push_str(&format!("\\u{scalar:04x}"));
                } else {
                    let adjusted = scalar - 0x1_0000;
                    let high = 0xd800 + (adjusted >> 10);
                    let low = 0xdc00 + (adjusted & 0x3ff);
                    output.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
                }
            }
            item => output.push(item),
        }
    }
    output.push('"');
    output
}

fn render_compact(value: &Value, ensure_ascii: bool, spaced: bool) -> String {
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(item) => item.to_string(),
        Value::Number(item) => item.to_string(),
        Value::String(item) => escape_json_string(item, ensure_ascii),
        Value::Array(items) => {
            let separator = if spaced { ", " } else { "," };
            format!(
                "[{}]",
                items
                    .iter()
                    .map(|item| render_compact(item, ensure_ascii, spaced))
                    .collect::<Vec<_>>()
                    .join(separator)
            )
        }
        Value::Object(items) => {
            let separator = if spaced { ", " } else { "," };
            let colon = if spaced { ": " } else { ":" };
            let mut keys = items.keys().collect::<Vec<_>>();
            keys.sort();
            format!(
                "{{{}}}",
                keys.into_iter()
                    .map(|key| format!(
                        "{}{}{}",
                        escape_json_string(key, ensure_ascii),
                        colon,
                        render_compact(&items[key], ensure_ascii, spaced)
                    ))
                    .collect::<Vec<_>>()
                    .join(separator)
            )
        }
    }
}

fn render_pretty(value: &Value, ensure_ascii: bool, depth: usize) -> String {
    match value {
        Value::Array(items) if !items.is_empty() => {
            let next = "  ".repeat(depth + 1);
            let current = "  ".repeat(depth);
            let body = items
                .iter()
                .map(|item| format!("{next}{}", render_pretty(item, ensure_ascii, depth + 1)))
                .collect::<Vec<_>>()
                .join(",\n");
            format!("[\n{body}\n{current}]")
        }
        Value::Object(items) if !items.is_empty() => {
            let next = "  ".repeat(depth + 1);
            let current = "  ".repeat(depth);
            let mut keys = items.keys().collect::<Vec<_>>();
            keys.sort();
            let body = keys
                .into_iter()
                .map(|key| {
                    format!(
                        "{next}{}: {}",
                        escape_json_string(key, ensure_ascii),
                        render_pretty(&items[key], ensure_ascii, depth + 1)
                    )
                })
                .collect::<Vec<_>>()
                .join(",\n");
            format!("{{\n{body}\n{current}}}")
        }
        _ => render_compact(value, ensure_ascii, false),
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096)
            / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    (year, month, day)
}

fn utc_now_iso() -> String {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let seconds = i64::try_from(duration.as_secs()).unwrap_or(i64::MAX);
    let days = seconds.div_euclid(86_400);
    let second_of_day = seconds.rem_euclid(86_400);
    let hour = second_of_day / 3_600;
    let minute = (second_of_day % 3_600) / 60;
    let second = second_of_day % 60;
    let (year, month, day) = civil_from_days(days);
    let micros = duration.subsec_nanos() / 1_000;
    if micros == 0 {
        format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}+00:00")
    } else {
        format!(
            "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00"
        )
    }
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "ADAPTER_TARGET_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("ADAPTER_TARGET_PARENT_CREATE_FAILED:{error}"))?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let name = path
        .file_name()
        .and_then(|item| item.to_str())
        .unwrap_or("adapter-config");
    let temporary = parent.join(format!(
        ".{name}.syntavra-{}-{nonce}.tmp",
        std::process::id()
    ));
    let mut handle = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("ADAPTER_TARGET_TEMP_CREATE_FAILED:{error}"))?;
    handle
        .write_all(bytes)
        .map_err(|error| format!("ADAPTER_TARGET_TEMP_WRITE_FAILED:{error}"))?;
    handle
        .flush()
        .map_err(|error| format!("ADAPTER_TARGET_TEMP_FLUSH_FAILED:{error}"))?;
    drop(handle);
    #[cfg(windows)]
    if path.exists() {
        fs::remove_file(path)
            .map_err(|error| format!("ADAPTER_TARGET_REPLACE_REMOVE_FAILED:{error}"))?;
    }
    fs::rename(&temporary, path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("ADAPTER_TARGET_REPLACE_FAILED:{error}")
    })
}

fn is_executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(windows)]
    {
        true
    }
}

#[cfg(windows)]
fn candidate_names(name: &str) -> Vec<String> {
    if Path::new(name).extension().is_some() {
        return vec![name.to_owned()];
    }
    env::var("PATHEXT")
        .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_owned())
        .split(';')
        .filter(|value| !value.is_empty())
        .map(|value| format!("{name}{value}"))
        .collect()
}

#[cfg(not(windows))]
fn candidate_names(name: &str) -> Vec<String> {
    vec![name.to_owned()]
}

fn command_exists(name: &str) -> bool {
    env::split_paths(&env::var_os("PATH").unwrap_or_default()).any(|directory| {
        candidate_names(name)
            .into_iter()
            .any(|candidate| is_executable(&directory.join(candidate)))
    })
}

fn detected(record: &Value, project_root: &Path, home_root: &Path) -> bool {
    let command = record["detection_commands"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(command_exists);
    let path = record["config_paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|candidate| expanded_path(candidate, project_root, home_root))
        .any(|candidate| candidate.exists());
    command || path
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    super::native_platform_state::initialize(state_root)?;
    let runtime_root = state_root.join("unified");
    let receipts = runtime_root.join("adapter-receipts");
    let backups = runtime_root.join("adapter-backups");
    fs::create_dir_all(&receipts)
        .map_err(|error| format!("ADAPTER_RECEIPTS_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(&backups)
        .map_err(|error| format!("ADAPTER_BACKUPS_CREATE_FAILED:{error}"))?;

    let (adapter_id, path_argument, desired_argument, apply) = parse_arguments(arguments)?;
    let catalog = catalog()?;
    let record = adapter_record(&catalog, &adapter_id)?;
    let home_root = home();
    let target = expanded_path(&path_argument, project_root, &home_root);
    let allowed = record["config_paths"]
        .as_array()
        .ok_or_else(|| "ADAPTER_CONFIG_PATHS_INVALID".to_owned())?
        .iter()
        .filter_map(Value::as_str)
        .map(|candidate| expanded_path(candidate, project_root, &home_root))
        .collect::<Vec<_>>();
    if !allowed.iter().any(|candidate| candidate == &target) {
        return Err(format!(
            "ADAPTER_PATH_NOT_DECLARED:{}",
            target.to_string_lossy()
        ));
    }

    let current = if target.is_file() {
        let source = fs::read_to_string(&target)
            .map_err(|error| format!("ADAPTER_CURRENT_READ_FAILED:{error}"))?;
        serde_json::from_str::<Value>(&source)
            .map_err(|error| format!("ADAPTER_CURRENT_JSON_INVALID:{error}"))?
    } else {
        json!({})
    };
    let desired = load_json_object(&desired_argument)?;
    let merged = merge_json(&current, &desired);
    let changed = merged != current;
    let mut backup_path = String::new();

    if apply && changed {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("ADAPTER_TARGET_PARENT_CREATE_FAILED:{error}"))?;
        }
        if target.exists() {
            let existing = fs::read(&target)
                .map_err(|error| format!("ADAPTER_BACKUP_SOURCE_READ_FAILED:{error}"))?;
            let digest = sha256_hex(&existing);
            let name = target
                .file_name()
                .and_then(|item| item.to_str())
                .ok_or_else(|| "ADAPTER_TARGET_NAME_INVALID".to_owned())?;
            let backup = backups
                .join(&adapter_id)
                .join(format!("{name}.{digest}.bak"));
            if let Some(parent) = backup.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| format!("ADAPTER_BACKUP_PARENT_CREATE_FAILED:{error}"))?;
            }
            fs::copy(&target, &backup)
                .map_err(|error| format!("ADAPTER_BACKUP_COPY_FAILED:{error}"))?;
            backup_path = backup.to_string_lossy().into_owned();
        }
        let bytes = format!("{}\n", render_pretty(&merged, true, 0)).into_bytes();
        atomic_write(&target, &bytes)?;
    }

    let content_hash = sha256_hex(render_compact(&merged, true, true).as_bytes());
    let checks = json!({
        "declared_path": true,
        "valid_json": true,
        "changed": changed,
        "applied": apply && changed,
        "content_hash": content_hash,
    });
    let created_at = utc_now_iso();
    let changed_paths = if changed {
        vec![target.to_string_lossy().into_owned()]
    } else {
        Vec::new()
    };
    let capabilities = record["capabilities"].clone();
    let maturity = if apply { "Configured" } else { "Contract" };
    let detection = detected(record, project_root, &home_root) || (apply && target.exists());
    let body = json!({
        "adapter_id": adapter_id.clone(),
        "maturity": maturity,
        "operation": "configure-json",
        "ok": true,
        "detected": detection,
        "changed_paths": changed_paths.clone(),
        "capabilities": capabilities.clone(),
        "checks": checks.clone(),
        "created_at": created_at.clone(),
    });
    let receipt_id = format!(
        "sha256:{}",
        sha256_hex(render_compact(&body, false, false).as_bytes())
    );
    let rollback = if backup_path.is_empty() {
        json!({})
    } else {
        json!({
            "backup": backup_path,
            "target": target.to_string_lossy(),
        })
    };
    let receipt = json!({
        "receipt_id": receipt_id,
        "adapter_id": adapter_id,
        "maturity": maturity,
        "operation": "configure-json",
        "ok": true,
        "created_at": created_at,
        "detected": detection,
        "changed_paths": changed_paths,
        "capabilities": capabilities,
        "checks": checks,
        "rollback": rollback,
        "claim_boundary": CLAIM_BOUNDARY,
    });
    let destination = receipts.join(format!(
        "{}.json",
        receipt["receipt_id"]
            .as_str()
            .and_then(|value| value.strip_prefix("sha256:"))
            .ok_or_else(|| "ADAPTER_RECEIPT_ID_INVALID".to_owned())?
    ));
    fs::write(
        destination,
        format!("{}\n", render_pretty(&receipt, false, 0)),
    )
    .map_err(|error| format!("ADAPTER_RECEIPT_WRITE_FAILED:{error}"))?;
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::{merge_json, supports};
    use serde_json::json;

    #[test]
    fn routes_adapter_configure_only() {
        assert!(supports(&[
            "run".to_owned(),
            "adapter-configure".to_owned()
        ]));
        assert!(!supports(&["run".to_owned(), "adapters".to_owned()]));
    }

    #[test]
    fn recursively_merges_objects() {
        let current = json!({"outer": {"left": 1}, "keep": true});
        let desired = json!({"outer": {"right": 2}});
        assert_eq!(
            merge_json(&current, desired.as_object().unwrap()),
            json!({"outer": {"left": 1, "right": 2}, "keep": true})
        );
    }
}
