use std::collections::BTreeSet;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::UNIX_EPOCH;

use rusqlite::{Connection, OpenFlags};
use serde_json::json;

const MAXIMUM_DATABASE_BYTES: u64 = 64 * 1024 * 1024;
const MAXIMUM_PATH_BYTES: usize = 4096;
const SIDECAR_SUFFIXES: &[&str] = &["-journal", "-shm", "-wal"];
const EXPECTED_COLUMNS: &[(&str, &str, i64, i64)] = &[
    ("version", "INTEGER", 0, 1),
    ("name", "TEXT", 1, 0),
    ("identity", "TEXT", 1, 0),
    ("applied_at", "REAL", 1, 0),
];

#[derive(Debug, Clone, PartialEq, Eq)]
struct FileIdentity {
    bytes: u64,
    modified_nanos: u128,
}

fn metadata(path: &Path, prefix: &str) -> Result<Option<fs::Metadata>, String> {
    match fs::symlink_metadata(path) {
        Ok(value) => {
            if value.file_type().is_symlink() {
                return Err(format!("{prefix}_SYMLINK"));
            }
            Ok(Some(value))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(format!("{prefix}_METADATA_FAILED")),
    }
}

fn validate_logical_path(value: &str) -> Result<PathBuf, String> {
    if value.is_empty() || value.len() > MAXIMUM_PATH_BYTES || value.contains('\0') {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    let path = Path::new(value);
    if path.is_absolute() {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Normal(part) => normalized.push(part),
            _ => return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned()),
        }
    }
    if normalized.as_os_str().is_empty() {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    Ok(normalized)
}

fn database_path(project_root: &Path, logical: &str) -> Result<PathBuf, String> {
    if let Some(value) = metadata(project_root, "MIGRATION_PLAN_PROJECT_ROOT")? {
        if !value.is_dir() {
            return Err("MIGRATION_PLAN_PROJECT_ROOT_NOT_DIRECTORY".to_owned());
        }
    }
    let relative = validate_logical_path(logical)?;
    let mut current = project_root.to_path_buf();
    let parts = relative.components().collect::<Vec<_>>();
    for component in parts.iter().take(parts.len().saturating_sub(1)) {
        if let Component::Normal(part) = component {
            current.push(part);
            match metadata(&current, "MIGRATION_PLAN_DATABASE_PARENT")? {
                Some(value) if !value.is_dir() => {
                    return Err("MIGRATION_PLAN_DATABASE_PARENT_NOT_DIRECTORY".to_owned())
                }
                Some(_) => {}
                None => break,
            }
        }
    }
    Ok(project_root.join(relative))
}

fn reject_sidecars(database: &Path) -> Result<(), String> {
    let database_text = database.to_string_lossy();
    for suffix in SIDECAR_SUFFIXES {
        let sidecar = PathBuf::from(format!("{database_text}{suffix}"));
        if let Some(value) = metadata(&sidecar, "MIGRATION_PLAN_SIDECAR")? {
            if !value.is_file() {
                return Err("MIGRATION_PLAN_SIDECAR_NOT_FILE".to_owned());
            }
            return Err("MIGRATION_PLAN_SIDECAR_PRESENT".to_owned());
        }
    }
    Ok(())
}

fn source_identity(database: &Path) -> Result<FileIdentity, String> {
    let value = metadata(database, "MIGRATION_PLAN_DATABASE")?
        .ok_or_else(|| "MIGRATION_PLAN_DATABASE_DISAPPEARED".to_owned())?;
    if !value.is_file() {
        return Err("MIGRATION_PLAN_DATABASE_NOT_FILE".to_owned());
    }
    let modified_nanos = value
        .modified()
        .map_err(|_| "MIGRATION_PLAN_DATABASE_METADATA_FAILED".to_owned())?
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "MIGRATION_PLAN_DATABASE_METADATA_FAILED".to_owned())?
        .as_nanos();
    Ok(FileIdentity {
        bytes: value.len(),
        modified_nanos,
    })
}

fn open_database(database: &Path) -> Result<Connection, String> {
    let identity = source_identity(database)?;
    if identity.bytes > MAXIMUM_DATABASE_BYTES {
        return Err("MIGRATION_PLAN_DATABASE_TOO_LARGE".to_owned());
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
        | OpenFlags::SQLITE_OPEN_NO_MUTEX
        | OpenFlags::SQLITE_OPEN_URI;
    let connection = Connection::open_with_flags(database, flags)
        .map_err(|_| "MIGRATION_PLAN_DATABASE_OPEN_FAILED".to_owned())?;
    connection
        .execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|_| "MIGRATION_PLAN_QUERY_ONLY_FAILED".to_owned())?;
    let query_only: i64 = connection
        .query_row("PRAGMA query_only", [], |row| row.get(0))
        .map_err(|_| "MIGRATION_PLAN_QUERY_ONLY_FAILED".to_owned())?;
    if query_only != 1 {
        return Err("MIGRATION_PLAN_QUERY_ONLY_FAILED".to_owned());
    }
    Ok(connection)
}

fn table_exists(connection: &Connection) -> Result<bool, String> {
    let value: i64 = connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='syntavra_schema_migrations')",
            [],
            |row| row.get(0),
        )
        .map_err(|_| "MIGRATION_PLAN_SCHEMA_QUERY_FAILED".to_owned())?;
    Ok(value == 1)
}

fn validate_identity(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn current_version(connection: &Connection) -> Result<i64, String> {
    if !table_exists(connection)? {
        return Ok(0);
    }
    let mut columns = connection
        .prepare("PRAGMA table_info(syntavra_schema_migrations)")
        .map_err(|_| "MIGRATION_PLAN_SCHEMA_QUERY_FAILED".to_owned())?;
    let actual = columns
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?.to_ascii_uppercase(),
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(5)?,
            ))
        })
        .map_err(|_| "MIGRATION_PLAN_SCHEMA_QUERY_FAILED".to_owned())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "MIGRATION_PLAN_SCHEMA_QUERY_FAILED".to_owned())?;
    let expected = EXPECTED_COLUMNS
        .iter()
        .map(|(name, kind, not_null, primary_key)| {
            (
                (*name).to_owned(),
                (*kind).to_owned(),
                *not_null,
                *primary_key,
            )
        })
        .collect::<Vec<_>>();
    if actual != expected {
        return Err("MIGRATION_PLAN_SCHEMA_COLUMNS_INVALID".to_owned());
    }

    let mut statement = connection
        .prepare(
            "SELECT version,name,identity,applied_at FROM syntavra_schema_migrations ORDER BY version",
        )
        .map_err(|_| "MIGRATION_PLAN_SCHEMA_QUERY_FAILED".to_owned())?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, f64>(3)?,
            ))
        })
        .map_err(|_| "MIGRATION_PLAN_ROWS_INVALID".to_owned())?;
    let mut versions = BTreeSet::new();
    let mut current = 0_i64;
    for row in rows {
        let (version, name, identity, applied_at) =
            row.map_err(|_| "MIGRATION_PLAN_ROWS_INVALID".to_owned())?;
        if version < 1 || !versions.insert(version) {
            return Err("MIGRATION_PLAN_VERSION_INVALID".to_owned());
        }
        if name.is_empty() || name.len() > 1024 {
            return Err("MIGRATION_PLAN_NAME_INVALID".to_owned());
        }
        if !validate_identity(&identity) {
            return Err("MIGRATION_PLAN_IDENTITY_INVALID".to_owned());
        }
        if !applied_at.is_finite() || applied_at < 0.0 {
            return Err("MIGRATION_PLAN_APPLIED_AT_INVALID".to_owned());
        }
        current = version;
    }
    Ok(current)
}

pub fn migration_plan_json(project_root: &str, logical_database: &str) -> Result<String, String> {
    let root = Path::new(project_root);
    let database = database_path(root, logical_database)?;
    reject_sidecars(&database)?;
    if metadata(&database, "MIGRATION_PLAN_DATABASE")?.is_none() {
        return serde_json::to_string(&json!({
            "current_version": 0,
            "database": logical_database,
            "pending": [],
            "target_version": 0,
        }))
        .map_err(|_| "MIGRATION_PLAN_JSON_FAILED".to_owned());
    }

    let before = source_identity(&database)?;
    let connection = open_database(&database)?;
    let current = current_version(&connection)?;
    drop(connection);
    reject_sidecars(&database)?;
    if source_identity(&database)? != before {
        return Err("MIGRATION_PLAN_SOURCE_CHANGED".to_owned());
    }
    serde_json::to_string(&json!({
        "current_version": current,
        "database": logical_database,
        "pending": [],
        "target_version": current,
    }))
    .map_err(|_| "MIGRATION_PLAN_JSON_FAILED".to_owned())
}
