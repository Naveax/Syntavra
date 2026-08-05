#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "uninstall")
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn load_manifest(path: &Path) -> Value {
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(_) => return json!({}),
    };
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(value)) => Value::Object(value),
        Ok(_) | Err(_) => json!({}),
    }
}

fn remove_path(path: &Path) -> Result<(), String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("UNINSTALL_TARGET_METADATA_FAILED:{error}")),
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(path).map_err(|error| format!("UNINSTALL_TARGET_REMOVE_FAILED:{error}"))
    } else {
        fs::remove_file(path).map_err(|error| format!("UNINSTALL_TARGET_REMOVE_FAILED:{error}"))
    }
}

fn copy_file(source: &Path, destination: &Path) -> Result<(), String> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("UNINSTALL_RESTORE_PARENT_FAILED:{error}"))?;
    }
    fs::copy(source, destination)
        .map(|_| ())
        .map_err(|error| format!("UNINSTALL_RESTORE_FILE_FAILED:{error}"))
}

fn copy_directory(source: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination)
        .map_err(|error| format!("UNINSTALL_RESTORE_DIRECTORY_FAILED:{error}"))?;
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("UNINSTALL_RESTORE_READ_FAILED:{error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("UNINSTALL_RESTORE_ENTRY_FAILED:{error}"))?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        let metadata = entry
            .metadata()
            .map_err(|error| format!("UNINSTALL_RESTORE_METADATA_FAILED:{error}"))?;
        if metadata.is_dir() {
            copy_directory(&source_path, &destination_path)?;
        } else if metadata.is_file() {
            copy_file(&source_path, &destination_path)?;
        } else {
            return Err("UNINSTALL_RESTORE_TYPE_UNSUPPORTED".to_owned());
        }
    }
    Ok(())
}

fn restore_backup(source: &Path, destination: &Path) -> Result<(), String> {
    let metadata = match fs::metadata(source) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("UNINSTALL_BACKUP_METADATA_FAILED:{error}")),
    };
    if metadata.is_dir() {
        copy_directory(source, destination)
    } else if metadata.is_file() {
        copy_file(source, destination)
    } else {
        Err("UNINSTALL_BACKUP_TYPE_UNSUPPORTED".to_owned())
    }
}

pub fn execute(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    let metadata = fs::metadata(project_root)
        .map_err(|error| format!("UNINSTALL_PROJECT_RESOLVE_FAILED:{error}"))?;
    if !metadata.is_dir() {
        return Err("UNINSTALL_PROJECT_NOT_DIRECTORY".to_owned());
    }

    let dry_run = flag(arguments, "--dry-run");
    let manifest_path = project_root
        .join(".syntavra")
        .join("install")
        .join("manifest.json");
    if !manifest_path.is_file() {
        return Ok(json!({"ok": true, "changes": [], "reason": "not-installed"}));
    }

    let manifest = load_manifest(&manifest_path);
    let rows = manifest
        .get("changes")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut changes = Vec::<Value>::new();
    for row in rows.iter().rev() {
        let Some(path_value) = row.get("path").and_then(Value::as_str) else {
            continue;
        };
        let path = Path::new(path_value);
        let backup = row
            .get("backup")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        changes.push(json!({
            "path": path.to_string_lossy(),
            "restore": backup,
        }));
        if dry_run {
            continue;
        }
        remove_path(path)?;
        if let Some(backup) = backup {
            restore_backup(Path::new(backup), path)?;
        }
    }
    if !dry_run {
        remove_path(&manifest_path)?;
    }
    Ok(json!({
        "ok": true,
        "dry_run": dry_run,
        "changes": changes,
    }))
}

#[cfg(test)]
mod tests {
    use super::{execute, supports};
    use std::fs;

    #[test]
    fn absent_manifest_is_idempotent() {
        let root =
            std::env::temp_dir().join(format!("syntavra-native-uninstall-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("project");
        let value = execute(&[], &root).expect("uninstall");
        assert_eq!(value["reason"], "not-installed");
        assert!(supports(&["uninstall".to_owned()]));
        let _ = fs::remove_dir_all(root);
    }
}
