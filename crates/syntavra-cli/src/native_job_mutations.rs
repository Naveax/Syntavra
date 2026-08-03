#![forbid(unsafe_code)]

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

const FINAL_STATES: [&str; 5] = ["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "ORPHANED"];
const JOB_COLUMNS: &str = "job_id,state,argv_json,cwd,created_at,started_at,completed_at,pid,exit_code,\
 timed_out,cancelled,summary,evidence_handle,error,project_id,repository_tree,environment_hash";

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
            .map_err(|_| "JOB_MUTATION_ARGV_JSON_INVALID".to_owned())?;
        if !argv.is_array() {
            return Err("JOB_MUTATION_ARGV_JSON_INVALID".to_owned());
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

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "job" && matches!(action.as_str(), "cancel" | "recover"))
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "JOB_MUTATION_SYSTEM_CLOCK_INVALID".to_owned())
}

fn initialize(state_root: &Path) -> Result<Connection, String> {
    let broker_root = state_root.join("broker");
    fs::create_dir_all(&broker_root)
        .map_err(|error| format!("JOB_MUTATION_DIRECTORY_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(broker_root.join("broker.sqlite3"))
        .map_err(|error| format!("JOB_MUTATION_DATABASE_OPEN_FAILED:{error}"))?;
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
             INSERT INTO metadata(key,value) VALUES('schema_version','2')\
               ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        )
        .map_err(|error| format!("JOB_MUTATION_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn positional_job_id(arguments: &[String], action: &str) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "job" && window[1] == action)
        .map(|window| window[2].as_str())
        .ok_or_else(|| format!("JOB_{}_ID_MISSING", action.to_ascii_uppercase()))
}

fn show(connection: &Connection, job_id: &str) -> Result<Value, String> {
    let sql = format!("SELECT {JOB_COLUMNS} FROM jobs WHERE job_id=?1");
    connection
        .query_row(&sql, [job_id], JobRow::from_sql)
        .optional()
        .map_err(|error| format!("JOB_MUTATION_SHOW_FAILED:{error}"))?
        .ok_or_else(|| "JOB_NOT_FOUND".to_owned())?
        .into_json()
}

#[cfg(unix)]
fn terminate_tree(pid: i64) {
    let _ = Command::new("kill")
        .args(["-TERM", "--", &format!("-{pid}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(windows)]
fn terminate_tree(pid: i64) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(any(unix, windows)))]
fn terminate_tree(_pid: i64) {}

#[cfg(unix)]
fn pid_alive(pid: i64) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(windows)]
fn pid_alive(pid: i64) -> bool {
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}")])
        .stdin(Stdio::null())
        .output()
        .is_ok_and(|output| String::from_utf8_lossy(&output.stdout).contains(&pid.to_string()))
}

#[cfg(not(any(unix, windows)))]
fn pid_alive(_pid: i64) -> bool {
    false
}

fn cancel(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let job_id = positional_job_id(arguments, "cancel")?;
    let connection = initialize(state_root)?;
    let current = show(&connection, job_id)?;
    let state = current
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| "JOB_MUTATION_STATE_INVALID".to_owned())?;
    if FINAL_STATES.contains(&state) {
        return Ok(current);
    }
    let marker = state_root.join("broker").join("jobs").join(job_id).join("cancel");
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(&marker)
        .map_err(|error| format!("JOB_CANCEL_MARKER_CREATE_FAILED:{error}"))?;
    if let Some(pid) = current.get("pid").and_then(Value::as_i64) {
        terminate_tree(pid);
    }
    let changed = connection
        .execute("UPDATE jobs SET cancelled=1 WHERE job_id=?1", [job_id])
        .map_err(|error| format!("JOB_CANCEL_UPDATE_FAILED:{error}"))?;
    if changed != 1 {
        return Err("JOB_NOT_FOUND".to_owned());
    }
    show(&connection, job_id)
}

fn append_completion(
    connection: &Connection,
    state_root: &Path,
    payload: &Value,
) -> Result<(), String> {
    let job_id = payload["job_id"]
        .as_str()
        .ok_or_else(|| "JOB_RECOVER_COMPLETION_ID_INVALID".to_owned())?;
    let state = payload["state"]
        .as_str()
        .ok_or_else(|| "JOB_RECOVER_COMPLETION_STATE_INVALID".to_owned())?;
    let completed_at = payload["completed_at"]
        .as_f64()
        .ok_or_else(|| "JOB_RECOVER_COMPLETION_TIME_INVALID".to_owned())?;
    let evidence_handle = payload["evidence_handle"].as_str().unwrap_or("");
    let payload_json = serde_json::to_string(payload)
        .map_err(|_| "JOB_RECOVER_COMPLETION_JSON_INVALID".to_owned())?;
    connection
        .execute(
            "INSERT INTO completion_events(job_id,state,exit_code,completed_at,evidence_handle,payload_json)\
             VALUES(?1,?2,NULL,?3,?4,?5)\
             ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,exit_code=excluded.exit_code,\
             completed_at=excluded.completed_at,evidence_handle=excluded.evidence_handle,payload_json=excluded.payload_json",
            params![job_id, state, completed_at, evidence_handle, payload_json],
        )
        .map_err(|error| format!("JOB_RECOVER_COMPLETION_INSERT_FAILED:{error}"))?;
    let path = state_root.join("broker").join("completions.jsonl");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("JOB_RECOVER_COMPLETION_FILE_OPEN_FAILED:{error}"))?;
    file.write_all(payload_json.as_bytes())
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.flush())
        .map_err(|error| format!("JOB_RECOVER_COMPLETION_FILE_WRITE_FAILED:{error}"))?;
    Ok(())
}

fn recover(state_root: &Path) -> Result<Value, String> {
    let connection = initialize(state_root)?;
    let sql = format!(
        "SELECT {JOB_COLUMNS} FROM jobs WHERE state IN ('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 10000"
    );
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("JOB_RECOVER_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], JobRow::from_sql)
        .map_err(|error| format!("JOB_RECOVER_QUERY_FAILED:{error}"))?;
    let mut candidates = Vec::new();
    for row in rows {
        candidates.push(row.map_err(|error| format!("JOB_RECOVER_ROW_FAILED:{error}"))?);
    }
    drop(statement);

    let mut orphaned = Vec::new();
    for candidate in candidates {
        if candidate.pid.is_some_and(pid_alive) {
            continue;
        }
        let job_id = candidate.job_id.clone();
        let evidence_handle = candidate.evidence_handle.clone();
        let completed_at = now()?;
        let changed = connection
            .execute(
                "UPDATE jobs SET state='ORPHANED',completed_at=?1,error='worker or process disappeared' WHERE job_id=?2",
                params![completed_at, job_id],
            )
            .map_err(|error| format!("JOB_RECOVER_UPDATE_FAILED:{error}"))?;
        if changed != 1 {
            return Err("JOB_NOT_FOUND".to_owned());
        }
        let payload = json!({
            "job_id": job_id,
            "state": "ORPHANED",
            "exit_code": Value::Null,
            "completed_at": completed_at,
            "evidence_handle": evidence_handle,
        });
        append_completion(&connection, state_root, &payload)?;
        orphaned.push(show(&connection, &job_id)?);
    }
    Ok(json!({"orphaned": orphaned}))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    match command {
        [root, action] if root == "job" && action == "cancel" => cancel(arguments, state_root),
        [root, action] if root == "job" && action == "recover" => recover(state_root),
        _ => Err("JOB_MUTATION_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn broker_job_mutations_are_supported() {
        assert!(supports(&["job".to_owned(), "cancel".to_owned()]));
        assert!(supports(&["job".to_owned(), "recover".to_owned()]));
    }
}
