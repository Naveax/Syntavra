#![forbid(unsafe_code)]

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "telemetry" && action == "bundle")
}

fn destination(arguments: &[String]) -> Result<PathBuf, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "telemetry" && window[1] == "bundle")
        .ok_or_else(|| "TELEMETRY_BUNDLE_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 2)
        .map(PathBuf::from)
        .ok_or_else(|| "TELEMETRY_BUNDLE_PATH_MISSING".to_owned())
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "TELEMETRY_SYSTEM_CLOCK_INVALID".to_owned())
}

fn log_tail(path: &Path, limit: usize) -> Result<Vec<Value>, String> {
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let text =
        fs::read_to_string(path).map_err(|error| format!("TELEMETRY_LOG_READ_FAILED:{error}"))?;
    let lines = text.lines().collect::<Vec<_>>();
    let start = lines.len().saturating_sub(limit);
    Ok(lines[start..]
        .iter()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .collect())
}

fn temporary_path(destination: &Path) -> Result<PathBuf, String> {
    let parent = destination.parent().unwrap_or_else(|| Path::new("."));
    let name = destination
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("telemetry-bundle.json");
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "TELEMETRY_SYSTEM_CLOCK_INVALID".to_owned())?
        .as_nanos();
    Ok(parent.join(format!(".{name}.{stamp}.tmp")))
}

fn atomic_write_json(destination: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("TELEMETRY_BUNDLE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let temporary = temporary_path(destination)?;
    let mut bytes =
        serde_json::to_vec(value).map_err(|_| "TELEMETRY_BUNDLE_JSON_RENDER_FAILED".to_owned())?;
    bytes.push(b'\n');
    let result = (|| {
        let mut handle = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(|error| format!("TELEMETRY_BUNDLE_TEMP_CREATE_FAILED:{error}"))?;
        handle
            .write_all(&bytes)
            .map_err(|error| format!("TELEMETRY_BUNDLE_WRITE_FAILED:{error}"))?;
        handle
            .sync_all()
            .map_err(|error| format!("TELEMETRY_BUNDLE_SYNC_FAILED:{error}"))?;
        drop(handle);
        if destination.exists() {
            fs::remove_file(destination)
                .map_err(|error| format!("TELEMETRY_BUNDLE_REPLACE_FAILED:{error}"))?;
        }
        fs::rename(&temporary, destination)
            .map_err(|error| format!("TELEMETRY_BUNDLE_REPLACE_FAILED:{error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let destination = destination(arguments)?;
    let observability_root = state_root.join("observability");
    fs::create_dir_all(&observability_root)
        .map_err(|error| format!("TELEMETRY_DIRECTORY_CREATE_FAILED:{error}"))?;
    let bundle = json!({
        "schema_version": 1,
        "generated_at": now()?,
        "service": "syntavra",
        "metrics": {
            "counters": [],
            "gauges": [],
            "histograms": [],
        },
        "extra": {},
        "log_tail": log_tail(&observability_root.join("events.jsonl"), 200)?,
    });
    atomic_write_json(&destination, &bundle)?;
    Ok(json!({"path": destination.to_string_lossy()}))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn telemetry_bundle_is_supported() {
        assert!(supports(&["telemetry".to_owned(), "bundle".to_owned(),]));
    }
}
