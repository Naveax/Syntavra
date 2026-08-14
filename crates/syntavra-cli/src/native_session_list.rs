#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use rusqlite::{Connection, Row};
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "session" && action == "list")
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_LIST_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_LIST_DATABASE_OPEN_FAILED:{error}"))?;
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
             CREATE INDEX IF NOT EXISTS session_summary_range_idx \
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
        .map_err(|error| format!("SESSION_LIST_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn state_filter(arguments: &[String]) -> Result<Option<String>, String> {
    let mut state = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == "--state" {
            index += 1;
            state = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| "SESSION_LIST_STATE_MISSING".to_owned())?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix("--state=") {
            state = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(state.filter(|value| !value.is_empty()))
}

fn session_row(
    row: &Row<'_>,
) -> rusqlite::Result<(String, String, String, String, f64, f64, String)> {
    Ok((
        row.get(0)?,
        row.get(1)?,
        row.get(2)?,
        row.get(3)?,
        row.get(4)?,
        row.get(5)?,
        row.get(6)?,
    ))
}

fn decode_array(text: &str, code: &str) -> Result<Value, String> {
    let value: Value = serde_json::from_str(text).map_err(|_| code.to_owned())?;
    if !value.is_array() {
        return Err(code.to_owned());
    }
    Ok(value)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::super::state_snapshot_contract::project_id_for_root(&project)?;
    let filter = state_filter(arguments)?;
    let connection = initialize(&state_root.join("sessions.sqlite3"))?;
    let mut sql = String::from(
        "SELECT session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json \
         FROM sessions WHERE project_id=?1",
    );
    if filter.is_some() {
        sql.push_str(" AND state=?2");
    }
    sql.push_str(" ORDER BY updated_at DESC");
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("SESSION_LIST_PREPARE_FAILED:{error}"))?;
    let mut sessions = Vec::new();
    if let Some(filter) = filter {
        let rows = statement
            .query_map([project_id.as_str(), filter.as_str()], session_row)
            .map_err(|error| format!("SESSION_LIST_QUERY_FAILED:{error}"))?;
        for row in rows {
            sessions.push(session_json(
                row.map_err(|error| format!("SESSION_LIST_ROW_FAILED:{error}"))?,
            )?);
        }
    } else {
        let rows = statement
            .query_map([project_id.as_str()], session_row)
            .map_err(|error| format!("SESSION_LIST_QUERY_FAILED:{error}"))?;
        for row in rows {
            sessions.push(session_json(
                row.map_err(|error| format!("SESSION_LIST_ROW_FAILED:{error}"))?,
            )?);
        }
    }
    Ok(json!({"sessions": sessions}))
}

fn session_json(row: (String, String, String, String, f64, f64, String)) -> Result<Value, String> {
    let (session_id, project_id, parent_ids_json, state, created_at, updated_at, metadata_json) =
        row;
    let metadata: Value = serde_json::from_str(&metadata_json)
        .map_err(|_| "SESSION_LIST_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "session_id": session_id,
        "project_id": project_id,
        "parent_ids": decode_array(&parent_ids_json, "SESSION_LIST_PARENT_IDS_INVALID")?,
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": metadata,
    }))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn session_list_command_is_supported() {
        assert!(supports(&["session".to_owned(), "list".to_owned()]));
    }
}
