#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use serde_json::{json, Value};

const CATALOG: &str =
    include_str!("../../../contracts/engine/r38-native-adapter-catalog-v1.json");

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "adapters")
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn home() -> PathBuf {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_default()
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

fn find_command(name: &str) -> Option<String> {
    for directory in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
        for candidate in candidate_names(name) {
            let path = directory.join(candidate);
            if is_executable(&path) {
                return Some(path.to_string_lossy().into_owned());
            }
        }
    }
    None
}

fn config_path(candidate: &str, project_root: &Path) -> PathBuf {
    if let Some(relative) = candidate.strip_prefix("~/") {
        return home().join(relative);
    }
    let path = PathBuf::from(candidate);
    if path.is_absolute() {
        path
    } else {
        project_root.join(path)
    }
}

fn detected_record(record: &Value, project_root: &Path) -> Value {
    let commands = record["detection_commands"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|command| find_command(command).is_some())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let configs = record["config_paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|candidate| config_path(candidate, project_root))
        .filter(|path| path.exists())
        .map(|path| path.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    let mut value = record.clone();
    if let Some(object) = value.as_object_mut() {
        object.insert(
            "detected".to_owned(),
            Value::Bool(!commands.is_empty() || !configs.is_empty()),
        );
        object.insert("detected_commands".to_owned(), json!(commands));
        object.insert("existing_configs".to_owned(), json!(configs));
    }
    value
}

pub fn execute(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    let catalog = serde_json::from_str::<Value>(CATALOG)
        .map_err(|error| format!("ADAPTER_CATALOG_INVALID:{error}"))?;
    let records = catalog["records"]
        .as_array()
        .ok_or_else(|| "ADAPTER_CATALOG_RECORDS_INVALID".to_owned())?;
    let adapters = if flag(arguments, "--detect") {
        records
            .iter()
            .map(|record| detected_record(record, project_root))
            .collect::<Vec<_>>()
    } else {
        records.clone()
    };
    Ok(json!({
        "ok": true,
        "validation": catalog["validation"],
        "adapters": adapters,
    }))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_run_adapters_only() {
        assert!(supports(&["run".to_owned(), "adapters".to_owned()]));
        assert!(!supports(&[
            "run".to_owned(),
            "adapter-configure".to_owned()
        ]));
    }
}
