#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::Connection;
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "migrate" && matches!(action.as_str(), "apply" | "rollback"))
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    offset: usize,
) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == action)
        .ok_or_else(|| "MIGRATION_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + offset)
        .map(String::as_str)
        .ok_or_else(|| format!("MIGRATION_ARGUMENT_MISSING:{action}:{offset}"))
}

fn current_version(path: &Path) -> Result<i64, String> {
    if !path.exists() {
        return Ok(0);
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("MIGRATION_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS syntavra_schema_migrations(\
             version INTEGER PRIMARY KEY,name TEXT NOT NULL,identity TEXT NOT NULL,applied_at REAL NOT NULL);",
        )
        .map_err(|error| format!("MIGRATION_TABLE_INITIALIZE_FAILED:{error}"))?;
    connection
        .query_row(
            "SELECT COALESCE(MAX(version),0) FROM syntavra_schema_migrations",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|error| format!("MIGRATION_VERSION_QUERY_FAILED:{error}"))
}

fn apply(arguments: &[String]) -> Result<Value, String> {
    let raw = positional_after(arguments, "apply", 1)?;
    let path = Path::new(raw);
    let version = current_version(path)?;
    Ok(json!({
        "database": raw,
        "before_version": version,
        "after_version": version,
        "applied": [],
        "backup_path": "",
        "duration_ms": 0.0,
        "ok": true,
    }))
}

fn remove_sidecars(path: &Path) -> Result<(), String> {
    for suffix in ["-wal", "-shm"] {
        let sidecar = PathBuf::from(format!("{}{suffix}", path.display()));
        match fs::remove_file(&sidecar) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!("MIGRATION_SIDECAR_REMOVE_FAILED:{suffix}:{error}"));
            }
        }
    }
    Ok(())
}

fn rollback(arguments: &[String]) -> Result<Value, String> {
    let database = PathBuf::from(positional_after(arguments, "rollback", 1)?);
    let backup = PathBuf::from(positional_after(arguments, "rollback", 2)?);
    let metadata =
        fs::metadata(&backup).map_err(|_| "MIGRATION_BACKUP_DOES_NOT_EXIST".to_owned())?;
    if !metadata.is_file() {
        return Err("MIGRATION_BACKUP_DOES_NOT_EXIST".to_owned());
    }
    if let Some(parent) = database
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)
            .map_err(|error| format!("MIGRATION_RESTORE_DIRECTORY_FAILED:{error}"))?;
    }
    remove_sidecars(&database)?;
    let temporary = database.with_file_name(format!(
        "{}.restore",
        database
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| "MIGRATION_DATABASE_NAME_INVALID".to_owned())?
    ));
    fs::copy(&backup, &temporary)
        .map_err(|error| format!("MIGRATION_RESTORE_COPY_FAILED:{error}"))?;
    if database.exists() {
        fs::remove_file(&database)
            .map_err(|error| format!("MIGRATION_RESTORE_REMOVE_FAILED:{error}"))?;
    }
    fs::rename(&temporary, &database).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("MIGRATION_RESTORE_REPLACE_FAILED:{error}")
    })?;
    Ok(json!({"ok": true}))
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    match command.get(1).map(String::as_str) {
        Some("apply") => apply(arguments),
        Some("rollback") => rollback(arguments),
        _ => Err("MIGRATION_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn migration_mutation_paths_are_supported() {
        for action in ["apply", "rollback"] {
            let command = ["migrate", action]
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            assert!(supports(&command));
        }
    }
}
