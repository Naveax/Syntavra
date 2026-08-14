#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use rusqlite::types::Value as SqlValue;
use rusqlite::{params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "job" && matches!(action.as_str(), "list" | "show" | "completions"))
}

#[derive(Debug)]
struct JobRow {
    job_id: String,
    state: String,
    argv_json: String,
    cwd: String,
    created_at: f64,
    started_at: Option<f64>,
    completed_at: Option<f64>,
    pid: Option<i64>,
    exit_code: Option<i64>,
    timed_out: i64,
    cancelled: i64,
    summary: String,
    evidence_handle: String,
    error: String,
    project_id: String,
    repository_tree: String,
    environment_hash: String,
}

impl JobRow {
    fn from_sql(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            job_id: row.get(0)?,
            state: row.get(1)?,
            argv_json: row.get(2)?,
            cwd: row.get(3)?,
            created_at: row.get(4)?,
            started_at: row.get(5)?,
            completed_at: row.get(6)?,
            pid: row.get(7)?,
            exit_code: row.get(8)?,
            timed_out: row.get(9)?,
            cancelled: row.get(10)?,
            summary: row.get(11)?,
            evidence_handle: row.get(12)?,
            error: row.get(13)?,
            project_id: row.get(14)?,
            repository_tree: row.get(15)?,
            environment_hash: row.get(16)?,
        })
    }

    fn into_json(self) -> Result<Value, String> {
        let argv: Value = serde_json::from_str(&self.argv_json)
            .map_err(|_| "JOB_ARGV_JSON_INVALID".to_owned())?;
        if !argv.is_array() {
            return Err("JOB_ARGV_JSON_INVALID".to_owned());
        }
        Ok(json!({
            "job_id": self.job_id,
            "state": self.state,
            "argv": argv,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out != 0,
            "cancelled": self.cancelled != 0,
            "summary": self.summary,
            "evidence_handle": self.evidence_handle,
            "error": self.error,
            "project_id": self.project_id,
            "repository_tree": self.repository_tree,
            "environment_hash": self.environment_hash,
        }))
    }
}

#[derive(Debug)]
struct CompletionRow {
    sequence: i64,
    job_id: String,
    state: String,
    exit_code: Option<i64>,
    completed_at: f64,
    evidence_handle: String,
}

impl CompletionRow {
    fn from_sql(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            sequence: row.get(0)?,
            job_id: row.get(1)?,
            state: row.get(2)?,
            exit_code: row.get(3)?,
            completed_at: row.get(4)?,
            evidence_handle: row.get(5)?,
        })
    }

    fn into_json(self) -> Value {
        json!({
            "sequence": self.sequence,
            "job_id": self.job_id,
            "state": self.state,
            "exit_code": self.exit_code,
            "completed_at": self.completed_at,
            "evidence_handle": self.evidence_handle,
        })
    }
}

fn initialize(state_root: &Path) -> Result<Connection, String> {
    let broker_root = state_root.join("broker");
    fs::create_dir_all(&broker_root)
        .map_err(|error| format!("JOB_BROKER_DIRECTORY_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(broker_root.join("broker.sqlite3"))
        .map_err(|error| format!("JOB_BROKER_DATABASE_OPEN_FAILED:{error}"))?;
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
        .map_err(|error| format!("JOB_BROKER_DATABASE_INITIALIZE_FAILED:{error}"))?;

    let has_project_id = {
        let mut statement = connection
            .prepare("PRAGMA table_info(jobs)")
            .map_err(|error| format!("JOB_BROKER_SCHEMA_INSPECT_FAILED:{error}"))?;
        let columns = statement
            .query_map([], |row| row.get::<_, String>(1))
            .map_err(|error| format!("JOB_BROKER_SCHEMA_INSPECT_FAILED:{error}"))?;
        let mut found = false;
        for column in columns {
            if column.map_err(|error| format!("JOB_BROKER_SCHEMA_ROW_FAILED:{error}"))?
                == "project_id"
            {
                found = true;
                break;
            }
        }
        found
    };
    if !has_project_id {
        connection
            .execute(
                "ALTER TABLE jobs ADD COLUMN project_id TEXT NOT NULL DEFAULT ''",
                [],
            )
            .map_err(|error| format!("JOB_BROKER_SCHEMA_MIGRATION_FAILED:{error}"))?;
    }
    Ok(connection)
}

fn repeated_option(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            values.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            values.push(value.to_owned());
        }
        index += 1;
    }
    Ok(values)
}

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    let mut value = default;
    let mut index = 0usize;
    while index < arguments.len() {
        let candidate = if arguments[index] == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .as_str(),
            )
        } else {
            arguments[index]
                .strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(candidate) = candidate {
            value = candidate
                .parse::<i64>()
                .map_err(|_| format!("{flag}_VALUE_INVALID"))?;
        }
        index += 1;
    }
    Ok(value)
}

fn positional_after<'a>(
    arguments: &'a [String],
    root: &str,
    action: &str,
) -> Result<&'a str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == root && window[1] == action)
        .map(|window| window[2].as_str())
        .ok_or_else(|| {
            format!(
                "{}_{}_ARGUMENT_MISSING",
                root.to_ascii_uppercase(),
                action.to_ascii_uppercase()
            )
        })
}

const JOB_COLUMNS: &str =
    "job_id,state,argv_json,cwd,created_at,started_at,completed_at,pid,exit_code,\
 timed_out,cancelled,summary,evidence_handle,error,project_id,repository_tree,environment_hash";

fn list(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let states = repeated_option(arguments, "--state")?;
    let limit = integer_option(arguments, "--limit", 100)?;
    let connection = initialize(state_root)?;
    let mut sql = format!("SELECT {JOB_COLUMNS} FROM jobs");
    let mut parameters = Vec::<SqlValue>::new();
    if !states.is_empty() {
        sql.push_str(" WHERE state IN (");
        sql.push_str(&vec!["?"; states.len()].join(","));
        sql.push(')');
        parameters.extend(states.into_iter().map(SqlValue::Text));
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT ?");
    parameters.push(SqlValue::Integer(limit));
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("JOB_LIST_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(params_from_iter(parameters.iter()), JobRow::from_sql)
        .map_err(|error| format!("JOB_LIST_QUERY_FAILED:{error}"))?;
    let mut jobs = Vec::new();
    for row in rows {
        jobs.push(
            row.map_err(|error| format!("JOB_LIST_ROW_FAILED:{error}"))?
                .into_json()?,
        );
    }
    Ok(json!({"jobs": jobs}))
}

fn show(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let job_id = positional_after(arguments, "job", "show")?;
    let connection = initialize(state_root)?;
    let sql = format!("SELECT {JOB_COLUMNS} FROM jobs WHERE job_id=?1");
    let row = connection
        .query_row(&sql, [job_id], JobRow::from_sql)
        .optional()
        .map_err(|error| format!("JOB_SHOW_QUERY_FAILED:{error}"))?
        .ok_or_else(|| "JOB_NOT_FOUND".to_owned())?;
    row.into_json()
}

fn completions(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let after = integer_option(arguments, "--after", 0)?.max(0);
    let limit = integer_option(arguments, "--limit", 100)?.max(1);
    let connection = initialize(state_root)?;
    let mut statement = connection
        .prepare(
            "SELECT sequence,job_id,state,exit_code,completed_at,evidence_handle \
             FROM completion_events WHERE sequence>?1 ORDER BY sequence LIMIT ?2",
        )
        .map_err(|error| format!("JOB_COMPLETIONS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([after, limit], CompletionRow::from_sql)
        .map_err(|error| format!("JOB_COMPLETIONS_QUERY_FAILED:{error}"))?;
    let mut events = Vec::new();
    let mut cursor = after;
    for row in rows {
        let row = row.map_err(|error| format!("JOB_COMPLETIONS_ROW_FAILED:{error}"))?;
        cursor = row.sequence;
        events.push(row.into_json());
    }
    Ok(json!({"cursor": cursor, "events": events}))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    match command {
        [root, action] if root == "job" && action == "list" => list(arguments, state_root),
        [root, action] if root == "job" && action == "show" => show(arguments, state_root),
        [root, action] if root == "job" && action == "completions" => {
            completions(arguments, state_root)
        }
        _ => Err("JOB_QUERY_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn broker_job_queries_are_supported() {
        assert!(supports(&["job".to_owned(), "list".to_owned()]));
        assert!(supports(&["job".to_owned(), "show".to_owned()]));
        assert!(supports(&["job".to_owned(), "completions".to_owned()]));
    }
}
