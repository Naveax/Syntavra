use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Map, Value};

const DATABASE_NAME: &str = "scheduler.sqlite3";
const MAXIMUM_DATABASE_BYTES: u64 = 64 * 1024 * 1024;
const MAXIMUM_LIMIT: usize = 1000;
const MAXIMUM_STATES: usize = 16;
const ALLOWED_STATES: &[&str] = &[
    "cancelled",
    "dead-letter",
    "failed",
    "queued",
    "running",
    "succeeded",
];
const SIDECAR_SUFFIXES: &[&str] = &["-journal", "-shm", "-wal"];
const JOB_COLUMNS: &[&str] = &[
    "job_id",
    "project_id",
    "argv_json",
    "priority",
    "state",
    "attempt",
    "max_attempts",
    "timeout_seconds",
    "sandbox_profile",
    "resource_class",
    "metadata_json",
    "scheduled_at",
    "created_at",
    "updated_at",
    "lease_owner",
    "lease_until",
    "last_error",
    "result_json",
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

fn state_database(state_root: &Path) -> Result<PathBuf, String> {
    if let Some(value) = metadata(state_root, "SCHEDULER_READ_ONLY_STATE_ROOT")? {
        if !value.is_dir() {
            return Err("SCHEDULER_READ_ONLY_STATE_ROOT_NOT_DIRECTORY".to_owned());
        }
    }
    Ok(state_root.join(DATABASE_NAME))
}

fn reject_sidecars(database: &Path) -> Result<(), String> {
    let database_text = database.to_string_lossy();
    for suffix in SIDECAR_SUFFIXES {
        let path = PathBuf::from(format!("{database_text}{suffix}"));
        if let Some(value) = metadata(&path, "SCHEDULER_READ_ONLY_SIDECAR")? {
            if !value.is_file() {
                return Err("SCHEDULER_READ_ONLY_SIDECAR_NOT_FILE".to_owned());
            }
            return Err("SCHEDULER_READ_ONLY_SIDECAR_PRESENT".to_owned());
        }
    }
    Ok(())
}

fn source_identity(database: &Path) -> Result<FileIdentity, String> {
    let value = metadata(database, "SCHEDULER_READ_ONLY_DATABASE")?
        .ok_or_else(|| "SCHEDULER_READ_ONLY_DATABASE_DISAPPEARED".to_owned())?;
    if !value.is_file() {
        return Err("SCHEDULER_READ_ONLY_DATABASE_NOT_FILE".to_owned());
    }
    let modified_nanos = value
        .modified()
        .map_err(|_| "SCHEDULER_READ_ONLY_DATABASE_METADATA_FAILED".to_owned())?
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SCHEDULER_READ_ONLY_DATABASE_METADATA_FAILED".to_owned())?
        .as_nanos();
    Ok(FileIdentity {
        bytes: value.len(),
        modified_nanos,
    })
}

fn open_database(database: &Path) -> Result<Connection, String> {
    let identity = source_identity(database)?;
    if identity.bytes > MAXIMUM_DATABASE_BYTES {
        return Err("SCHEDULER_READ_ONLY_DATABASE_TOO_LARGE".to_owned());
    }
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
        | OpenFlags::SQLITE_OPEN_NO_MUTEX
        | OpenFlags::SQLITE_OPEN_URI;
    let connection = Connection::open_with_flags(database, flags)
        .map_err(|_| "SCHEDULER_READ_ONLY_DATABASE_OPEN_FAILED".to_owned())?;
    connection
        .execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_ONLY_FAILED".to_owned())?;
    let query_only: i64 = connection
        .query_row("PRAGMA query_only", [], |row| row.get(0))
        .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_ONLY_FAILED".to_owned())?;
    if query_only != 1 {
        return Err("SCHEDULER_READ_ONLY_QUERY_ONLY_FAILED".to_owned());
    }
    Ok(connection)
}

fn table_columns(
    connection: &Connection,
    table: &str,
) -> Result<Vec<(String, String, i64, i64)>, String> {
    let sql = format!("PRAGMA table_info({table})");
    let mut statement = connection
        .prepare(&sql)
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?.to_ascii_uppercase(),
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(5)?,
            ))
        })
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())
}

fn validate_schema(connection: &Connection) -> Result<(), String> {
    let expected: BTreeMap<&str, Vec<(&str, &str, i64, i64)>> = BTreeMap::from([
        (
            "scheduled_jobs",
            vec![
                ("job_id", "TEXT", 0, 1),
                ("project_id", "TEXT", 1, 0),
                ("argv_json", "TEXT", 1, 0),
                ("priority", "INTEGER", 1, 0),
                ("state", "TEXT", 1, 0),
                ("attempt", "INTEGER", 1, 0),
                ("max_attempts", "INTEGER", 1, 0),
                ("timeout_seconds", "REAL", 1, 0),
                ("sandbox_profile", "TEXT", 1, 0),
                ("resource_class", "TEXT", 1, 0),
                ("metadata_json", "TEXT", 1, 0),
                ("scheduled_at", "REAL", 1, 0),
                ("created_at", "REAL", 1, 0),
                ("updated_at", "REAL", 1, 0),
                ("lease_owner", "TEXT", 1, 0),
                ("lease_until", "REAL", 1, 0),
                ("last_error", "TEXT", 1, 0),
                ("result_json", "TEXT", 1, 0),
            ],
        ),
        (
            "job_dependencies",
            vec![("job_id", "TEXT", 1, 1), ("dependency_id", "TEXT", 1, 2)],
        ),
        (
            "scheduler_events",
            vec![
                ("sequence", "INTEGER", 0, 1),
                ("job_id", "TEXT", 1, 0),
                ("event", "TEXT", 1, 0),
                ("payload_json", "TEXT", 1, 0),
                ("created_at", "REAL", 1, 0),
            ],
        ),
    ]);
    for (table, rows) in expected {
        let actual = table_columns(connection, table)?;
        let wanted = rows
            .into_iter()
            .map(|(name, kind, not_null, primary_key)| {
                (name.to_owned(), kind.to_owned(), not_null, primary_key)
            })
            .collect::<Vec<_>>();
        if actual != wanted {
            return Err("SCHEDULER_READ_ONLY_SCHEMA_COLUMNS_INVALID".to_owned());
        }
    }

    let mut statement = connection
        .prepare("SELECT name FROM sqlite_master WHERE type='index'")
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    let indexes = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?
        .collect::<Result<BTreeSet<_>, _>>()
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    if !indexes.contains("scheduled_jobs_ready_idx")
        || !indexes.contains("scheduled_jobs_project_idx")
    {
        return Err("SCHEDULER_READ_ONLY_SCHEMA_INDEX_INVALID".to_owned());
    }

    let mut foreign_keys = connection
        .prepare("PRAGMA foreign_key_list(job_dependencies)")
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    let rows = foreign_keys
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "SCHEDULER_READ_ONLY_SCHEMA_QUERY_FAILED".to_owned())?;
    if rows
        != vec![(
            "scheduled_jobs".to_owned(),
            "job_id".to_owned(),
            "job_id".to_owned(),
        )]
    {
        return Err("SCHEDULER_READ_ONLY_SCHEMA_FOREIGN_KEY_INVALID".to_owned());
    }
    Ok(())
}

fn sqlite_value(value: ValueRef<'_>) -> Result<Value, String> {
    match value {
        ValueRef::Null => Ok(Value::Null),
        ValueRef::Integer(value) => Ok(json!(value)),
        ValueRef::Real(value) => serde_json::Number::from_f64(value)
            .map(Value::Number)
            .ok_or_else(|| "SCHEDULER_READ_ONLY_ROW_NUMBER_INVALID".to_owned()),
        ValueRef::Text(value) => std::str::from_utf8(value)
            .map(|text| Value::String(text.to_owned()))
            .map_err(|_| "SCHEDULER_READ_ONLY_ROW_TEXT_INVALID".to_owned()),
        ValueRef::Blob(_) => Err("SCHEDULER_READ_ONLY_ROW_BLOB_INVALID".to_owned()),
    }
}

fn validate_job_json(job: &Map<String, Value>) -> Result<(), String> {
    for (name, object) in [
        ("argv_json", false),
        ("metadata_json", true),
        ("result_json", true),
    ] {
        let encoded = job
            .get(name)
            .and_then(Value::as_str)
            .ok_or_else(|| "SCHEDULER_READ_ONLY_ROW_JSON_INVALID".to_owned())?;
        let decoded: Value = serde_json::from_str(encoded)
            .map_err(|_| "SCHEDULER_READ_ONLY_ROW_JSON_INVALID".to_owned())?;
        if (object && !decoded.is_object()) || (!object && !decoded.is_array()) {
            return Err("SCHEDULER_READ_ONLY_ROW_JSON_INVALID".to_owned());
        }
    }
    Ok(())
}

fn canonical_states(states_json: &[u8]) -> Result<Vec<String>, String> {
    let values: Vec<String> = serde_json::from_slice(states_json)
        .map_err(|_| "SCHEDULER_READ_ONLY_STATE_INVALID".to_owned())?;
    if values.len() > MAXIMUM_STATES {
        return Err("SCHEDULER_READ_ONLY_TOO_MANY_STATES".to_owned());
    }
    let allowed = ALLOWED_STATES.iter().copied().collect::<BTreeSet<_>>();
    let mut output = BTreeSet::new();
    for value in values {
        let normalized = value.trim().to_ascii_lowercase();
        if !allowed.contains(normalized.as_str()) {
            return Err("SCHEDULER_READ_ONLY_STATE_INVALID".to_owned());
        }
        output.insert(normalized);
    }
    Ok(output.into_iter().collect())
}

fn empty_result(stats: bool) -> Value {
    if stats {
        json!({"database_integrity": true, "projects": 0, "states": {}})
    } else {
        json!({"jobs": []})
    }
}

fn inspect(
    state_root: &Path,
    stats: bool,
    states: &[String],
    limit: usize,
) -> Result<Value, String> {
    let database = state_database(state_root)?;
    reject_sidecars(&database)?;
    if metadata(&database, "SCHEDULER_READ_ONLY_DATABASE")?.is_none() {
        return Ok(empty_result(stats));
    }
    let identity_before = source_identity(&database)?;
    let connection = open_database(&database)?;
    validate_schema(&connection)?;

    let result = if stats {
        let mut state_statement = connection
            .prepare("SELECT state,COUNT(*) FROM scheduled_jobs GROUP BY state ORDER BY state")
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        let rows = state_statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            })
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        let mut counts = Map::new();
        for row in rows {
            let (state, count) = row.map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
            counts.insert(state, json!(count));
        }
        let projects: i64 = connection
            .query_row(
                "SELECT COUNT(DISTINCT project_id) FROM scheduled_jobs",
                [],
                |row| row.get(0),
            )
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        let integrity: String = connection
            .query_row("PRAGMA integrity_check", [], |row| row.get(0))
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        json!({
            "database_integrity": integrity == "ok",
            "projects": projects,
            "states": counts,
        })
    } else {
        let mut query = format!("SELECT {} FROM scheduled_jobs", JOB_COLUMNS.join(","));
        if !states.is_empty() {
            query.push_str(" WHERE state IN (");
            query.push_str(&vec!["?"; states.len()].join(","));
            query.push(')');
        }
        query.push_str(" ORDER BY created_at DESC,job_id DESC LIMIT ?");
        let mut parameters = states
            .iter()
            .map(|value| rusqlite::types::Value::Text(value.clone()))
            .collect::<Vec<_>>();
        parameters.push(rusqlite::types::Value::Integer(
            limit.clamp(1, MAXIMUM_LIMIT) as i64,
        ));
        let mut statement = connection
            .prepare(&query)
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        let rows = statement
            .query_map(rusqlite::params_from_iter(parameters.iter()), |row| {
                let mut job = Map::new();
                for (index, name) in JOB_COLUMNS.iter().enumerate() {
                    let value = sqlite_value(row.get_ref(index)?).map_err(|message| {
                        rusqlite::Error::FromSqlConversionFailure(
                            index,
                            rusqlite::types::Type::Null,
                            Box::new(std::io::Error::new(
                                std::io::ErrorKind::InvalidData,
                                message,
                            )),
                        )
                    })?;
                    job.insert((*name).to_owned(), value);
                }
                Ok(job)
            })
            .map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
        let mut jobs = Vec::new();
        for row in rows {
            let job = row.map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
            validate_job_json(&job)?;
            jobs.push(Value::Object(job));
        }
        json!({"jobs": jobs})
    };

    drop(connection);
    reject_sidecars(&database)?;
    if source_identity(&database)? != identity_before {
        return Err("SCHEDULER_READ_ONLY_SOURCE_CHANGED".to_owned());
    }
    Ok(result)
}

pub fn scheduler_stats_json(state_root: &str) -> Result<String, String> {
    serde_json::to_string(&inspect(Path::new(state_root), true, &[], 100)?)
        .map_err(|_| "SCHEDULER_READ_ONLY_JSON_FAILED".to_owned())
}

pub fn scheduler_list_json(
    state_root: &str,
    limit: usize,
    states_json: &[u8],
) -> Result<String, String> {
    let states = canonical_states(states_json)?;
    serde_json::to_string(&inspect(
        Path::new(state_root),
        false,
        &states,
        limit.clamp(1, MAXIMUM_LIMIT),
    )?)
    .map_err(|_| "SCHEDULER_READ_ONLY_JSON_FAILED".to_owned())
}
