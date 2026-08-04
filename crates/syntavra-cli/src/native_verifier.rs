#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "verifier" && matches!(action.as_str(), "lookup" | "invalidated-by"))
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("VERIFIER_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection =
        Connection::open(path).map_err(|error| format!("VERIFIER_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS jobs(\
               job_id TEXT PRIMARY KEY,state TEXT NOT NULL,argv_json TEXT NOT NULL,cwd TEXT NOT NULL,\
               created_at REAL NOT NULL,started_at REAL,completed_at REAL,pid INTEGER,exit_code INTEGER,\
               timed_out INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,\
               summary TEXT NOT NULL DEFAULT '',evidence_handle TEXT NOT NULL DEFAULT '',\
               error TEXT NOT NULL DEFAULT '',timeout_seconds REAL NOT NULL DEFAULT 0,\
               stdout_path TEXT NOT NULL DEFAULT '',stderr_path TEXT NOT NULL DEFAULT '',\
               repository_tree TEXT NOT NULL DEFAULT 'unknown',environment_hash TEXT NOT NULL DEFAULT 'unknown',\
               project_id TEXT NOT NULL DEFAULT '');\
             CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state,created_at DESC);\
             CREATE TABLE IF NOT EXISTS completion_events(\
               sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,\
               exit_code INTEGER,completed_at REAL NOT NULL,evidence_handle TEXT NOT NULL,payload_json TEXT NOT NULL,\
               FOREIGN KEY(job_id) REFERENCES jobs(job_id));\
             CREATE TABLE IF NOT EXISTS verifier_results(\
               cache_key TEXT PRIMARY KEY,command_json TEXT NOT NULL,tree_hash TEXT NOT NULL,\
               environment_hash TEXT NOT NULL,dependency_hash TEXT NOT NULL,toolchain_hash TEXT NOT NULL,\
               success INTEGER NOT NULL,exit_code INTEGER NOT NULL,evidence_handle TEXT NOT NULL,\
               affected_paths_json TEXT NOT NULL,created_at REAL NOT NULL);\
             INSERT INTO metadata(key,value) VALUES('schema_version','2') \
               ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        )
        .map_err(|error| format!("VERIFIER_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn option_value(arguments: &[String], flag: &str) -> Result<String, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            let value = arguments
                .get(index)
                .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?;
            if found.replace(value.clone()).is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            if found.replace(value.to_owned()).is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
        }
        index += 1;
    }
    found.ok_or_else(|| format!("{flag}_REQUIRED"))
}

fn command_arguments(arguments: &[String]) -> Result<Vec<String>, String> {
    let start = arguments
        .iter()
        .position(|value| value == "lookup")
        .and_then(|index| index.checked_add(1))
        .ok_or_else(|| "VERIFIER_LOOKUP_ACTION_MISSING".to_owned())?;
    let values = arguments[start..]
        .iter()
        .take_while(|value| !value.starts_with("--"))
        .cloned()
        .collect::<Vec<_>>();
    if values.is_empty() {
        return Err("VERIFIER_COMMAND_REQUIRED".to_owned());
    }
    Ok(values)
}

fn repeated_paths(arguments: &[String]) -> Result<Vec<String>, String> {
    let mut paths = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == "--path" {
            index += 1;
            paths.push(
                arguments
                    .get(index)
                    .ok_or_else(|| "--path_VALUE_MISSING".to_owned())?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix("--path=") {
            paths.push(value.to_owned());
        }
        index += 1;
    }
    if paths.is_empty() {
        return Err("--path_REQUIRED".to_owned());
    }
    Ok(paths)
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "VERIFIER_CANONICAL_JSON_FAILED".to_owned())
}

fn cache_key(
    command: &[String],
    tree_hash: &str,
    environment_hash: &str,
    dependency_hash: &str,
    toolchain_hash: &str,
) -> Result<String, String> {
    let payload = json!({
        "command": command,
        "tree_hash": tree_hash,
        "environment_hash": environment_hash,
        "dependency_hash": dependency_hash,
        "toolchain_hash": toolchain_hash,
    });
    Ok(sha256_hex(&canonical_bytes(&payload)?))
}

fn json_array(text: &str, code: &str) -> Result<Value, String> {
    let value: Value = serde_json::from_str(text).map_err(|_| code.to_owned())?;
    if !value.is_array() {
        return Err(code.to_owned());
    }
    Ok(value)
}

fn lookup(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let command = command_arguments(arguments)?;
    let tree_hash = option_value(arguments, "--tree-hash")?;
    let environment_hash = option_value(arguments, "--environment-hash")?;
    let dependency_hash = option_value(arguments, "--dependency-hash")?;
    let toolchain_hash = option_value(arguments, "--toolchain-hash")?;
    let key = cache_key(
        &command,
        &tree_hash,
        &environment_hash,
        &dependency_hash,
        &toolchain_hash,
    )?;
    let connection = initialize(&state_root.join("verifier.sqlite3"))?;
    let row = connection
        .query_row(
            "SELECT cache_key,command_json,tree_hash,environment_hash,dependency_hash,toolchain_hash,\
             success,exit_code,evidence_handle,affected_paths_json,created_at \
             FROM verifier_results WHERE cache_key=?1",
            [&key],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                    row.get::<_, f64>(10)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("VERIFIER_LOOKUP_FAILED:{error}"))?;
    let Some(row) = row else {
        return Ok(json!({"hit": false}));
    };
    Ok(json!({
        "cache_key": row.0,
        "command": json_array(&row.1, "VERIFIER_COMMAND_JSON_INVALID")?,
        "tree_hash": row.2,
        "environment_hash": row.3,
        "dependency_hash": row.4,
        "toolchain_hash": row.5,
        "success": row.6 != 0,
        "exit_code": row.7,
        "evidence_handle": row.8,
        "affected_paths": json_array(&row.9, "VERIFIER_AFFECTED_PATHS_JSON_INVALID")?,
        "created_at": row.10,
    }))
}

fn invalidated_by(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let changed = repeated_paths(arguments)?
        .into_iter()
        .collect::<BTreeSet<_>>();
    let connection = initialize(&state_root.join("verifier.sqlite3"))?;
    let mut statement = connection
        .prepare(
            "SELECT cache_key,command_json,affected_paths_json FROM verifier_results ORDER BY created_at DESC",
        )
        .map_err(|error| format!("VERIFIER_INVALIDATION_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })
        .map_err(|error| format!("VERIFIER_INVALIDATION_QUERY_FAILED:{error}"))?;
    let mut invalidated = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("VERIFIER_INVALIDATION_ROW_FAILED:{error}"))?;
        let command = json_array(&row.1, "VERIFIER_COMMAND_JSON_INVALID")?;
        let affected = json_array(&row.2, "VERIFIER_AFFECTED_PATHS_JSON_INVALID")?;
        let affected_set = affected
            .as_array()
            .ok_or_else(|| "VERIFIER_AFFECTED_PATHS_JSON_INVALID".to_owned())?
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect::<BTreeSet<_>>();
        let overlap = changed
            .intersection(&affected_set)
            .cloned()
            .collect::<Vec<_>>();
        if !overlap.is_empty() {
            invalidated.push(json!({
                "cache_key": row.0,
                "overlap": overlap,
                "command": command,
            }));
        }
    }
    Ok(json!({"invalidated": invalidated}))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    match command {
        [root, action] if root == "verifier" && action == "lookup" => lookup(arguments, state_root),
        [root, action] if root == "verifier" && action == "invalidated-by" => {
            invalidated_by(arguments, state_root)
        }
        _ => Err("VERIFIER_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn verifier_commands_are_supported() {
        assert!(supports(&["verifier".to_owned(), "lookup".to_owned()]));
        assert!(supports(&[
            "verifier".to_owned(),
            "invalidated-by".to_owned()
        ]));
    }
}
