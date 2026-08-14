#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use rusqlite::{Connection, Row};
use serde_json::{json, Value};

const MAX_ANALYTICS_BYTES: u64 = 64 * 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "session-status")
}

fn initialize_database(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection =
        Connection::open(path).map_err(|error| format!("SESSION_STATE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA foreign_keys=ON;\
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
             CREATE INDEX IF NOT EXISTS session_summary_range_idx ON session_summaries(session_id,source_start,source_end);\
             CREATE TABLE IF NOT EXISTS session_checkpoints(\
               checkpoint_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,through_sequence INTEGER NOT NULL,\
               root_summary_id TEXT,event_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,created_at REAL NOT NULL,\
               FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE TABLE IF NOT EXISTS session_quarantine(\
               quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,\
               object_type TEXT NOT NULL,object_id TEXT NOT NULL,reason TEXT NOT NULL,\
               payload_json TEXT NOT NULL,created_at REAL NOT NULL);",
        )
        .map_err(|error| format!("SESSION_STATE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
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

fn sessions(connection: &Connection, project_id: &str) -> Result<Vec<Value>, String> {
    let mut statement = connection
        .prepare(
            "SELECT session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json \
             FROM sessions WHERE project_id=?1 ORDER BY updated_at DESC",
        )
        .map_err(|error| format!("SESSION_STATUS_QUERY_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([project_id], session_row)
        .map_err(|error| format!("SESSION_STATUS_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let (session_id, project_id, parents, state, created_at, updated_at, metadata) =
            row.map_err(|error| format!("SESSION_STATUS_ROW_FAILED:{error}"))?;
        let parent_ids: Value = serde_json::from_str(&parents)
            .map_err(|_| "SESSION_STATUS_PARENT_IDS_INVALID".to_owned())?;
        if !parent_ids.is_array() {
            return Err("SESSION_STATUS_PARENT_IDS_INVALID".to_owned());
        }
        let metadata: Value = serde_json::from_str(&metadata)
            .map_err(|_| "SESSION_STATUS_METADATA_INVALID".to_owned())?;
        if !metadata.is_object() {
            return Err("SESSION_STATUS_METADATA_INVALID".to_owned());
        }
        output.push(json!({
            "session_id": session_id,
            "project_id": project_id,
            "parent_ids": parent_ids,
            "state": state,
            "created_at": created_at,
            "updated_at": updated_at,
            "metadata": metadata,
        }));
    }
    Ok(output)
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[allow(clippy::cast_possible_truncation)]
fn truncated_i64(number: f64) -> Option<i64> {
    let truncated = number.trunc();
    if !truncated.is_finite()
        || !(-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&truncated)
    {
        return None;
    }
    Some(truncated as i64)
}

fn python_int(value: Option<&Value>) -> Result<i64, String> {
    match value {
        None => Ok(0),
        Some(Value::Bool(value)) => Ok(i64::from(*value)),
        Some(Value::Number(value)) => value
            .as_i64()
            .or_else(|| value.as_u64().and_then(|number| i64::try_from(number).ok()))
            .or_else(|| value.as_f64().and_then(truncated_i64))
            .ok_or_else(|| "SESSION_ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(Value::String(value)) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| "SESSION_ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(_) => Err("SESSION_ANALYTICS_INTEGER_INVALID".to_owned()),
    }
}

fn python_float(value: Option<&Value>) -> Result<f64, String> {
    let number = match value {
        None => 0.0,
        Some(Value::Bool(value)) => f64::from(u8::from(*value)),
        Some(Value::Number(value)) => value
            .as_f64()
            .ok_or_else(|| "SESSION_ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(Value::String(value)) => value
            .trim()
            .parse::<f64>()
            .map_err(|_| "SESSION_ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(_) => return Err("SESSION_ANALYTICS_FLOAT_INVALID".to_owned()),
    };
    if number.is_finite() {
        Ok(number)
    } else {
        Err("SESSION_ANALYTICS_FLOAT_NONFINITE".to_owned())
    }
}

fn identity_string(value: &Value) -> Option<String> {
    if !json_truthy(value) {
        return None;
    }
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Bool(value) => Some(if *value { "True" } else { "False" }.to_owned()),
        Value::Number(value) => Some(value.to_string()),
        Value::Null => None,
        Value::Array(_) | Value::Object(_) => serde_json::to_string(value).ok(),
    }
}

fn analytics(path: &Path) -> Result<Value, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_ANALYTICS_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => Vec::new(),
        Err(error) => return Err(format!("SESSION_ANALYTICS_READ_FAILED:{error}")),
    };
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_ANALYTICS_BYTES {
        return Err("SESSION_ANALYTICS_FILE_TOO_LARGE".to_owned());
    }
    let text = String::from_utf8(bytes).map_err(|_| "SESSION_ANALYTICS_UTF8_INVALID".to_owned())?;
    let mut rows = Vec::<Value>::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value =
            serde_json::from_str(line).map_err(|_| "SESSION_ANALYTICS_JSONL_INVALID".to_owned())?;
        if value.is_object() {
            rows.push(value);
        }
    }

    let mut session_ids = BTreeSet::new();
    let mut repositories = BTreeSet::new();
    let mut input_tokens = 0_i64;
    let mut cached_tokens = 0_i64;
    let mut output_tokens = 0_i64;
    let mut wall_time_ms = 0.0_f64;
    let mut cost_usd = 0.0_f64;
    let mut compaction_ms = 0.0_f64;
    let mut continuity = 0_u64;
    let mut route_denied = 0_u64;

    for row in &rows {
        let object = row
            .as_object()
            .ok_or_else(|| "SESSION_ANALYTICS_ROW_INVALID".to_owned())?;
        if let Some(value) = object.get("session_id").and_then(identity_string) {
            session_ids.insert(value);
        }
        if let Some(value) = object.get("repository_hash").and_then(identity_string) {
            repositories.insert(value);
        }
        input_tokens = input_tokens.saturating_add(python_int(object.get("input_tokens"))?.max(0));
        cached_tokens =
            cached_tokens.saturating_add(python_int(object.get("cached_input_tokens"))?.max(0));
        output_tokens =
            output_tokens.saturating_add(python_int(object.get("output_tokens"))?.max(0));
        wall_time_ms += python_float(object.get("wall_time_ms"))?.max(0.0);
        cost_usd += python_float(object.get("cost_usd"))?.max(0.0);
        compaction_ms += python_float(object.get("compaction_ms"))?.max(0.0);
        continuity += u64::from(object.get("continuity_restored").is_some_and(json_truthy));
        route_denied += u64::from(matches!(
            object.get("tool_route_allowed"),
            Some(Value::Bool(false))
        ));
    }

    Ok(json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "events": rows.len(),
        "sessions": session_ids.len(),
        "repositories": repositories.len(),
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "billable_input_tokens": input_tokens.saturating_sub(cached_tokens).max(0),
            "output_tokens": output_tokens,
            "wall_time_ms": wall_time_ms,
            "cost_usd": cost_usd,
        },
        "continuity": {
            "restores": continuity,
            "compaction_wall_time_ms": compaction_ms,
        },
        "routing": {"denied": route_denied},
        "privacy": "content-free local aggregate",
    }))
}

pub fn execute(project_root: &Path, state_root: &Path) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::state_snapshot_contract::project_id_for_root(&project)?;
    let connection = initialize_database(&state_root.join("sessions.sqlite3"))?;
    let sessions = sessions(&connection, &project_id)?;
    let analytics = analytics(&state_root.join("analytics").join("events.jsonl"))?;
    Ok(json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "worker_alive": false,
        "last_cycle": {
            "state": "IDLE",
            "started_at": null,
            "completed_at": null,
            "wall_time_ms": 0.0,
            "compacted": 0,
            "failures": [],
        },
        "analytics": analytics,
        "sessions": sessions,
    }))
}

#[cfg(test)]
mod tests {
    use super::{json_truthy, supports};
    use serde_json::json;

    #[test]
    fn session_status_command_is_supported() {
        let command = ["run", "session-status"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert!(supports(&command));
    }

    #[test]
    fn json_truth_matches_python_container_rules() {
        assert!(!json_truthy(&json!([])));
        assert!(json_truthy(&json!([1])));
        assert!(!json_truthy(&json!(0)));
        assert!(json_truthy(&json!("x")));
    }
}
