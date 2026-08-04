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
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS scheduled_jobs(\
               job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,argv_json TEXT NOT NULL,\
               priority INTEGER NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL,\
               max_attempts INTEGER NOT NULL,timeout_seconds REAL NOT NULL,\
               sandbox_profile TEXT NOT NULL,resource_class TEXT NOT NULL,\
               metadata_json TEXT NOT NULL,scheduled_at REAL NOT NULL,created_at REAL NOT NULL,\
               updated_at REAL NOT NULL,lease_owner TEXT NOT NULL DEFAULT '',\
               lease_until REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',\
               result_json TEXT NOT NULL DEFAULT '{}');\
             CREATE INDEX IF NOT EXISTS scheduled_jobs_ready_idx \
               ON scheduled_jobs(state,scheduled_at,priority,created_at);\
             CREATE INDEX IF NOT EXISTS scheduled_jobs_project_idx \
               ON scheduled_jobs(project_id,state);\
             CREATE TABLE IF NOT EXISTS job_dependencies(\
               job_id TEXT NOT NULL,dependency_id TEXT NOT NULL,\
               PRIMARY KEY(job_id,dependency_id),\
               FOREIGN KEY(job_id) REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE);\
             CREATE TABLE IF NOT EXISTS scheduler_events(\
               sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,\
               event TEXT NOT NULL,payload_json TEXT NOT NULL,created_at REAL NOT NULL);",
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
    let rows = {
        let mut statement = transaction
            .prepare(
                "SELECT job_id,attempt,max_attempts FROM scheduled_jobs \
                 WHERE state='running' AND lease_until>0 AND lease_until<=?1",
            )
            .map_err(|error| format!("SCHEDULER_REAP_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([now], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })
            .map_err(|error| format!("SCHEDULER_REAP_QUERY_FAILED:{error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("SCHEDULER_REAP_ROW_FAILED:{error}"))?;
        rows
    };

    for (job_id, attempt, max_attempts) in &rows {
        let next_state = if attempt < max_attempts {
            "queued"
        } else {
            "dead-letter"
        };
        transaction
            .execute(
                "UPDATE scheduled_jobs SET state=?1,lease_owner='',lease_until=0,\
                 scheduled_at=?2,last_error='lease-expired',updated_at=?2 WHERE job_id=?3",
                params![next_state, now, job_id],
            )
            .map_err(|error| format!("SCHEDULER_REAP_UPDATE_FAILED:{error}"))?;
        let payload = format!("{{\"next_state\": \"{next_state}\"}}");
        transaction
            .execute(
                "INSERT INTO scheduler_events(job_id,event,payload_json,created_at) \
                 VALUES(?1,'lease-expired',?2,?3)",
                params![job_id, payload, now_seconds()?],
            )
            .map_err(|error| format!("SCHEDULER_REAP_EVENT_FAILED:{error}"))?;
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
