#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "scheduler" && action == "reap")
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SCHEDULER_SYSTEM_CLOCK_INVALID".to_owned())
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SCHEDULER_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SCHEDULER_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA synchronous=FULL;\
             CREATE TABLE IF NOT EXISTS jobs(\
               job_id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_json TEXT NOT NULL,state TEXT NOT NULL,\
               priority INTEGER NOT NULL,created_at REAL NOT NULL,available_at REAL NOT NULL,\
               started_at REAL,finished_at REAL,lease_expires_at REAL,worker_id TEXT,attempts INTEGER NOT NULL,\
               max_attempts INTEGER NOT NULL,retry_backoff_seconds REAL NOT NULL,idempotency_key TEXT UNIQUE,\
               result_json TEXT,error TEXT,metadata_json TEXT NOT NULL);\
             CREATE INDEX IF NOT EXISTS jobs_ready_idx ON jobs(state,available_at,priority,created_at);\
             CREATE INDEX IF NOT EXISTS jobs_lease_idx ON jobs(state,lease_expires_at);\
             CREATE TABLE IF NOT EXISTS dead_letters(\
               dead_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,kind TEXT NOT NULL,\
               payload_json TEXT NOT NULL,attempts INTEGER NOT NULL,error TEXT,failed_at REAL NOT NULL,\
               metadata_json TEXT NOT NULL);\
             CREATE INDEX IF NOT EXISTS dead_job_idx ON dead_letters(job_id);",
        )
        .map_err(|error| format!("SCHEDULER_SCHEMA_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

pub fn execute(state_root: &Path) -> Result<Value, String> {
    let mut connection = initialize(&state_root.join("scheduler.sqlite3"))?;
    let now = now_seconds()?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SCHEDULER_REAP_TRANSACTION_FAILED:{error}"))?;
    let mut statement = transaction
        .prepare(
            "SELECT job_id,attempts,max_attempts,retry_backoff_seconds \
             FROM jobs WHERE state='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?1",
        )
        .map_err(|error| format!("SCHEDULER_REAP_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([now], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, f64>(3)?,
            ))
        })
        .map_err(|error| format!("SCHEDULER_REAP_QUERY_FAILED:{error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("SCHEDULER_REAP_ROW_FAILED:{error}"))?;
    drop(statement);

    for (job_id, attempts, max_attempts, retry_backoff_seconds) in &rows {
        if attempts >= max_attempts {
            transaction
                .execute(
                    "UPDATE jobs SET state='dead-letter',finished_at=?1,lease_expires_at=NULL,\
                     worker_id=NULL,error='lease expired' WHERE job_id=?2",
                    params![now, job_id],
                )
                .map_err(|error| format!("SCHEDULER_REAP_DEAD_UPDATE_FAILED:{error}"))?;
            transaction
                .execute(
                    "INSERT INTO dead_letters(job_id,kind,payload_json,attempts,error,failed_at,metadata_json) \
                     SELECT job_id,kind,payload_json,attempts,error,?1,metadata_json FROM jobs WHERE job_id=?2",
                    params![now, job_id],
                )
                .map_err(|error| format!("SCHEDULER_REAP_DEAD_INSERT_FAILED:{error}"))?;
        } else {
            transaction
                .execute(
                    "UPDATE jobs SET state='queued',available_at=?1,lease_expires_at=NULL,\
                     worker_id=NULL,error='lease expired' WHERE job_id=?2",
                    params![now + retry_backoff_seconds, job_id],
                )
                .map_err(|error| format!("SCHEDULER_REAP_REQUEUE_FAILED:{error}"))?;
        }
    }
    transaction
        .commit()
        .map_err(|error| format!("SCHEDULER_REAP_COMMIT_FAILED:{error}"))?;
    Ok(json!({"reaped": rows.len()}))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn scheduler_reap_is_supported() {
        assert!(supports(&["scheduler".to_owned(), "reap".to_owned()]));
    }
}
