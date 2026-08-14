#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::params;
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action]
        if fabric == "fabric" && action == "rollback-install")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "rollback-install")
        .map(|index| index + 2)
        .ok_or_else(|| "FABRIC_ROLLBACK_INSTALL_COMMAND_MISSING".to_owned())
}

fn transaction_id(arguments: &[String]) -> Result<String, String> {
    let index = command_start(arguments)?;
    let value = arguments
        .get(index)
        .ok_or_else(|| "fabric rollback-install transaction_id is required".to_owned())?;
    if value.starts_with('-') {
        return Err("fabric rollback-install transaction_id is required".to_owned());
    }
    Ok(value.clone())
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            index += 1;
            found = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{name}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

fn restore_backup(backup: &Path, target: &Path) -> Result<(), String> {
    if backup.is_dir() {
        return super::native_fabric_install::copy_tree(backup, target);
    }
    if backup.is_file() {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_ROLLBACK_INSTALL_PARENT_FAILED:{error}"))?;
        }
        fs::copy(backup, target)
            .map_err(|error| format!("FABRIC_ROLLBACK_INSTALL_COPY_FAILED:{error}"))?;
        return Ok(());
    }
    Err(format!(
        "FABRIC_ROLLBACK_INSTALL_BACKUP_MISSING:{}",
        backup.to_string_lossy()
    ))
}

fn rollback_value(
    transaction_id: &str,
    host: &str,
    scope: &str,
    root: &Path,
    created_at: f64,
    original: &Value,
) -> Result<Value, String> {
    let changes = original["changes"]
        .as_array()
        .ok_or_else(|| "FABRIC_ROLLBACK_INSTALL_CHANGES_INVALID".to_owned())?;
    let mut rolled = Vec::with_capacity(changes.len());
    for raw in changes.iter().rev() {
        let relative = raw["path"]
            .as_str()
            .ok_or_else(|| "FABRIC_ROLLBACK_INSTALL_PATH_INVALID".to_owned())?;
        let target = super::native_fabric_install::safe_target(root, relative)?;
        super::native_fabric_install::remove_target(&target)?;
        let existed = raw["existed"].as_bool().unwrap_or(false);
        let backup_path = raw["backup_path"].as_str().unwrap_or_default();
        if existed && !backup_path.is_empty() {
            restore_backup(Path::new(backup_path), &target)?;
        }
        rolled.push(json!({
            "path": relative,
            "kind": raw["kind"],
            "action": if existed { "restored" } else { "removed" },
            "existed": existed,
            "before_hash": raw["after_hash"],
            "after_hash": super::native_fabric_install::digest(&target)?,
            "backup_path": backup_path,
        }));
    }
    rolled.reverse();
    Ok(json!({
        "transaction_id": transaction_id,
        "host": host,
        "scope": scope,
        "root": root.to_string_lossy(),
        "status": "rolled-back",
        "changes": rolled,
        "verification": {"ok": true, "rolled_back": true},
        "created_at": created_at,
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let id = transaction_id(arguments)?;
    let database_path = state_root.join("host-installations.sqlite3");
    if !database_path.is_file() {
        return Err(format!(
            "FABRIC_ROLLBACK_INSTALL_TRANSACTION_NOT_FOUND:{id}"
        ));
    }
    let connection = super::native_fabric_install::initialize_database(&database_path)?;
    let row = connection
        .query_row(
            "SELECT host,scope,root,status,manifest_json,created_at \
             FROM host_install_transactions WHERE transaction_id=?",
            params![id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, f64>(5)?,
                ))
            },
        )
        .map_err(|error| format!("FABRIC_ROLLBACK_INSTALL_TRANSACTION_NOT_FOUND:{id}:{error}"))?;
    let original = serde_json::from_str::<Value>(&row.4)
        .map_err(|error| format!("FABRIC_ROLLBACK_INSTALL_MANIFEST_INVALID:{error}"))?;
    let value = if row.3 == "rolled-back" {
        original
    } else {
        let root = PathBuf::from(&row.2);
        let value = rollback_value(&id, &row.0, &row.1, &root, row.5, &original)?;
        connection
            .execute(
                "UPDATE host_install_transactions \
                 SET status=?,manifest_json=?,updated_at=? WHERE transaction_id=?",
                params![
                    "rolled-back",
                    serde_json::to_string(&value).map_err(|error| {
                        format!("FABRIC_ROLLBACK_INSTALL_MANIFEST_SERIALIZE_FAILED:{error}")
                    })?,
                    super::native_fabric_install::now()?,
                    id,
                ],
            )
            .map_err(|error| format!("FABRIC_ROLLBACK_INSTALL_UPDATE_FAILED:{error}"))?;
        value
    };
    option_value(arguments, "--output")?.map_or_else(
        || Ok(value.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &value),
    )
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_rollback_install_only() {
        assert!(supports(&[
            "fabric".to_owned(),
            "rollback-install".to_owned()
        ]));
        assert!(!supports(&["fabric".to_owned(), "install".to_owned()]));
    }
}
