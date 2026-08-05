#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::Connection;
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "installations")
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

fn configured_skill_root(arguments: &[String]) -> Result<Option<PathBuf>, String> {
    option_value(arguments, "--skill-root")?
        .map(PathBuf::from)
        .map(|path| {
            path.canonicalize().map_err(|error| {
                format!(
                    "FABRIC_INSTALLATIONS_SKILL_ROOT_RESOLVE_FAILED:{}:{error}",
                    path.to_string_lossy()
                )
            })
        })
        .transpose()
}

fn bundled_skill_root(project_root: &Path) -> PathBuf {
    let project = project_root.join("skills").join("syntavra");
    if project.join("SKILL.md").is_file() {
        return project;
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .unwrap_or_else(|| Path::new("."))
        .join("skills")
        .join("syntavra")
}

fn validate_skill_root(path: &Path) -> Result<(), String> {
    if !path.join("SKILL.md").is_file() {
        return Err(format!(
            "Syntavra skill source is incomplete: {}",
            path.to_string_lossy()
        ));
    }
    Ok(())
}

fn initialize_database(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("FABRIC_INSTALLATIONS_DATABASE_PARENT_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("FABRIC_INSTALLATIONS_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS jobs(\
                job_id TEXT PRIMARY KEY,state TEXT NOT NULL,argv_json TEXT NOT NULL,cwd TEXT NOT NULL,\
                created_at REAL NOT NULL,started_at REAL,completed_at REAL,pid INTEGER,exit_code INTEGER,\
                timed_out INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,\
                summary TEXT NOT NULL DEFAULT '',evidence_handle TEXT NOT NULL DEFAULT '',\
                error TEXT NOT NULL DEFAULT '',timeout_seconds REAL NOT NULL DEFAULT 0,\
                stdout_path TEXT NOT NULL DEFAULT '',stderr_path TEXT NOT NULL DEFAULT '',\
                repository_tree TEXT NOT NULL DEFAULT 'unknown',environment_hash TEXT NOT NULL DEFAULT 'unknown',\
                project_id TEXT NOT NULL DEFAULT ''\
             );\
             CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at DESC);\
             CREATE TABLE IF NOT EXISTS completion_events(\
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,\
                exit_code INTEGER,completed_at REAL NOT NULL,evidence_handle TEXT NOT NULL,payload_json TEXT NOT NULL,\
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)\
             );\
             CREATE TABLE IF NOT EXISTS verifier_results(\
                cache_key TEXT PRIMARY KEY,command_json TEXT NOT NULL,tree_hash TEXT NOT NULL,\
                environment_hash TEXT NOT NULL,dependency_hash TEXT NOT NULL,toolchain_hash TEXT NOT NULL,\
                success INTEGER NOT NULL,exit_code INTEGER NOT NULL,evidence_handle TEXT NOT NULL,\
                affected_paths_json TEXT NOT NULL,created_at REAL NOT NULL\
             );\
             CREATE TABLE IF NOT EXISTS host_install_transactions(\
                transaction_id TEXT PRIMARY KEY,host TEXT NOT NULL,scope TEXT NOT NULL,root TEXT NOT NULL,\
                status TEXT NOT NULL,manifest_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL\
             );\
             CREATE INDEX IF NOT EXISTS host_install_host_idx \
                ON host_install_transactions(host,scope,created_at);\
             INSERT INTO metadata(key,value) VALUES('schema_version','2') \
                ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        )
        .map_err(|error| format!("FABRIC_INSTALLATIONS_DATABASE_SCHEMA_FAILED:{error}"))?;
    Ok(connection)
}

fn rows(
    connection: &Connection,
    host: Option<&str>,
    limit: i64,
) -> Result<Vec<Value>, String> {
    let mut output = Vec::new();
    if let Some(host) = host.filter(|value| !value.is_empty()) {
        let mut statement = connection
            .prepare(
                "SELECT transaction_id,host,scope,root,status,created_at,updated_at \
                 FROM host_install_transactions WHERE host=? \
                 ORDER BY created_at DESC LIMIT ?",
            )
            .map_err(|error| format!("FABRIC_INSTALLATIONS_PREPARE_FAILED:{error}"))?;
        let records = statement
            .query_map((host.to_lowercase(), limit), |row| {
                Ok(json!({
                    "transaction_id": row.get::<_, String>(0)?,
                    "host": row.get::<_, String>(1)?,
                    "scope": row.get::<_, String>(2)?,
                    "root": row.get::<_, String>(3)?,
                    "status": row.get::<_, String>(4)?,
                    "created_at": row.get::<_, f64>(5)?,
                    "updated_at": row.get::<_, f64>(6)?,
                }))
            })
            .map_err(|error| format!("FABRIC_INSTALLATIONS_QUERY_FAILED:{error}"))?;
        for record in records {
            output.push(
                record.map_err(|error| format!("FABRIC_INSTALLATIONS_ROW_FAILED:{error}"))?,
            );
        }
    } else {
        let mut statement = connection
            .prepare(
                "SELECT transaction_id,host,scope,root,status,created_at,updated_at \
                 FROM host_install_transactions ORDER BY created_at DESC LIMIT ?",
            )
            .map_err(|error| format!("FABRIC_INSTALLATIONS_PREPARE_FAILED:{error}"))?;
        let records = statement
            .query_map([limit], |row| {
                Ok(json!({
                    "transaction_id": row.get::<_, String>(0)?,
                    "host": row.get::<_, String>(1)?,
                    "scope": row.get::<_, String>(2)?,
                    "root": row.get::<_, String>(3)?,
                    "status": row.get::<_, String>(4)?,
                    "created_at": row.get::<_, f64>(5)?,
                    "updated_at": row.get::<_, f64>(6)?,
                }))
            })
            .map_err(|error| format!("FABRIC_INSTALLATIONS_QUERY_FAILED:{error}"))?;
        for record in records {
            output.push(
                record.map_err(|error| format!("FABRIC_INSTALLATIONS_ROW_FAILED:{error}"))?,
            );
        }
    }
    Ok(output)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let configured = configured_skill_root(arguments)?;
    let database = initialize_database(&state_root.join("host-installations.sqlite3"))?;
    fs::create_dir_all(state_root.join("host-installations"))
        .map_err(|error| format!("FABRIC_INSTALLATIONS_STORAGE_FAILED:{error}"))?;
    let selected = configured.unwrap_or_else(|| bundled_skill_root(project_root));
    validate_skill_root(&selected)?;

    let requested_limit = option_value(arguments, "--limit")?
        .unwrap_or_else(|| "20".to_owned())
        .parse::<i64>()
        .map_err(|error| format!("--limit_INVALID:{error}"))?;
    let limit = requested_limit.clamp(1, 500);
    let host = option_value(arguments, "--host-name")?;
    let value = Value::Array(rows(&database, host.as_deref(), limit)?);
    option_value(arguments, "--output")?.map_or_else(
        || Ok(value.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &value),
    )
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_fabric_installations_only() {
        assert!(supports(&[
            "fabric".to_owned(),
            "installations".to_owned()
        ]));
        assert!(!supports(&[
            "fabric".to_owned(),
            "install".to_owned()
        ]));
    }
}
