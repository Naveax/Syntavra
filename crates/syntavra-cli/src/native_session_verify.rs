#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use rusqlite::{Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "session" && action == "verify")
}

#[derive(Debug)]
struct EventRow {
    session_id: String,
    sequence: i64,
    event_type: String,
    payload_json: String,
    previous_hash: String,
    event_hash: String,
    created_at: f64,
}

impl EventRow {
    fn from_sql(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            session_id: row.get(0)?,
            sequence: row.get(1)?,
            event_type: row.get(2)?,
            payload_json: row.get(3)?,
            previous_hash: row.get(4)?,
            event_hash: row.get(5)?,
            created_at: row.get(6)?,
        })
    }
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_VERIFY_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_VERIFY_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS sessions(\
               session_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,parent_ids_json TEXT NOT NULL,\
               state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,metadata_json TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS session_events(\
               session_id TEXT NOT NULL,sequence INTEGER NOT NULL,event_type TEXT NOT NULL,\
               payload_json TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL,\
               created_at REAL NOT NULL,PRIMARY KEY(session_id,sequence),UNIQUE(session_id,event_hash),\
               FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS session_event_hash_idx ON session_events(event_hash);\
             CREATE TABLE IF NOT EXISTS session_summaries(\
               summary_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,content TEXT NOT NULL,\
               source_start INTEGER NOT NULL,source_end INTEGER NOT NULL,child_ids_json TEXT NOT NULL,\
               source_hash TEXT NOT NULL,order_level INTEGER NOT NULL,created_at REAL NOT NULL,\
               invalidated_at REAL,FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS session_summary_range_idx\
               ON session_summaries(session_id,source_start,source_end);\
             CREATE TABLE IF NOT EXISTS session_checkpoints(\
               checkpoint_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,through_sequence INTEGER NOT NULL,\
               root_summary_id TEXT,event_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,created_at REAL NOT NULL,\
               FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE TABLE IF NOT EXISTS session_quarantine(\
               quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,\
               object_type TEXT NOT NULL,object_id TEXT NOT NULL,reason TEXT NOT NULL,\
               payload_json TEXT NOT NULL,created_at REAL NOT NULL);",
        )
        .map_err(|error| format!("SESSION_VERIFY_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn session_id(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "session" && window[1] == "verify")
        .map(|window| window[2].as_str())
        .ok_or_else(|| "SESSION_VERIFY_ID_MISSING".to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_VERIFY_JSON_SERIALIZE_FAILED".to_owned())
}

fn verify(connection: &Connection, session_id: &str, project_id: &str) -> Result<Value, String> {
    let exists = connection
        .query_row(
            "SELECT 1 FROM sessions WHERE session_id=?1 AND project_id=?2",
            [session_id, project_id],
            |_| Ok(()),
        )
        .optional()
        .map_err(|error| format!("SESSION_VERIFY_SCOPE_QUERY_FAILED:{error}"))?
        .is_some();
    if !exists {
        return Err(format!("SESSION_NOT_FOUND:{session_id}"));
    }

    let mut statement = connection
        .prepare(
            "SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at \
             FROM session_events WHERE session_id=?1 ORDER BY sequence",
        )
        .map_err(|error| format!("SESSION_VERIFY_EVENT_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([session_id], EventRow::from_sql)
        .map_err(|error| format!("SESSION_VERIFY_EVENT_QUERY_FAILED:{error}"))?;

    let mut reasons = Vec::new();
    let mut previous = ZERO_HASH.to_owned();
    let mut expected_sequence = 1_i64;
    for row in rows {
        let event = row.map_err(|error| format!("SESSION_VERIFY_EVENT_ROW_FAILED:{error}"))?;
        let payload: Value = serde_json::from_str(&event.payload_json)
            .map_err(|_| "SESSION_VERIFY_EVENT_PAYLOAD_INVALID".to_owned())?;
        if event.sequence != expected_sequence {
            reasons.push(format!(
                "sequence-gap:{expected_sequence}->{}",
                event.sequence
            ));
        }
        if event.previous_hash != previous {
            reasons.push(format!("previous-hash-mismatch:{}", event.sequence));
        }
        let material = json!({
            "session_id": event.session_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": payload,
            "previous_hash": event.previous_hash,
            "created_at": event.created_at,
        });
        if sha256_hex(&canonical_bytes(&material)?) != event.event_hash {
            reasons.push(format!("event-hash-mismatch:{}", event.sequence));
        }
        previous = event.event_hash;
        expected_sequence = event.sequence + 1;
    }

    Ok(json!({
        "ok": reasons.is_empty(),
        "events": expected_sequence - 1,
        "last_hash": previous,
        "reasons": reasons,
    }))
}

fn emit_failure(value: &Value) -> ! {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    std::process::exit(3)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let session_id = session_id(arguments)?;
    let project = project_root.to_string_lossy();
    let project_id = super::super::state_snapshot_contract::project_id_for_root(&project)?;
    let connection = initialize(&state_root.join("sessions.sqlite3"))?;
    let value = verify(&connection, session_id, &project_id)?;
    if value.get("ok").and_then(Value::as_bool) == Some(false) {
        emit_failure(&value);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn session_verify_command_is_supported() {
        assert!(supports(&["session".to_owned(), "verify".to_owned()]));
    }
}
