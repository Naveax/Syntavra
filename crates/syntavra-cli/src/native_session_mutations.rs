#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const MAX_JSON_BYTES: u64 = 16 * 1024 * 1024;
const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "run" && matches!(action.as_str(), "session-open" | "session-append"))
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

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SESSION_SYSTEM_CLOCK_INVALID".to_owned())
}

fn generated_session_id() -> Result<String, String> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SESSION_SYSTEM_CLOCK_INVALID".to_owned())?;
    let material = format!(
        "{}:{}:{}",
        elapsed.as_nanos(),
        std::process::id(),
        std::thread::current().name().unwrap_or("unnamed")
    );
    Ok(format!("sess-{}", &sha256_hex(material.as_bytes())[..32]))
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let value = if arguments[index] == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            arguments[index]
                .strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(value) = value {
            if found.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            found = Some(value);
        }
        index += 1;
    }
    Ok(found)
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    offset: usize,
) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == action)
        .ok_or_else(|| "SESSION_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + offset)
        .map(String::as_str)
        .ok_or_else(|| format!("SESSION_ARGUMENT_MISSING:{action}:{offset}"))
}

fn load_json_object(source: &str, code: &str) -> Result<Value, String> {
    let path = Path::new(source);
    let bytes = if path.is_file() {
        let metadata =
            fs::symlink_metadata(path).map_err(|error| format!("{code}_INSPECT_FAILED:{error}"))?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(format!("{code}_SOURCE_INVALID"));
        }
        if metadata.len() > MAX_JSON_BYTES {
            return Err(format!("{code}_TOO_LARGE"));
        }
        fs::read(path).map_err(|error| format!("{code}_READ_FAILED:{error}"))?
    } else {
        if u64::try_from(source.len()).unwrap_or(u64::MAX) > MAX_JSON_BYTES {
            return Err(format!("{code}_TOO_LARGE"));
        }
        source.as_bytes().to_vec()
    };
    let value: Value =
        serde_json::from_slice(&bytes).map_err(|_| format!("{code}_JSON_INVALID"))?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(format!("{code}_OBJECT_REQUIRED"))
    }
}

fn session(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<Option<Value>, String> {
    let row = connection
        .query_row(
            "SELECT session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json \
             FROM sessions WHERE session_id=?1 AND project_id=?2",
            params![session_id, project_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, f64>(4)?,
                    row.get::<_, f64>(5)?,
                    row.get::<_, String>(6)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("SESSION_QUERY_FAILED:{error}"))?;
    let Some((session_id, project_id, parents, state, created_at, updated_at, metadata)) = row
    else {
        return Ok(None);
    };
    let parent_ids: Value =
        serde_json::from_str(&parents).map_err(|_| "SESSION_PARENT_IDS_INVALID".to_owned())?;
    let metadata: Value =
        serde_json::from_str(&metadata).map_err(|_| "SESSION_METADATA_INVALID".to_owned())?;
    Ok(Some(json!({
        "session_id": session_id,
        "project_id": project_id,
        "parent_ids": parent_ids,
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": metadata,
    })))
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_JSON_SERIALIZE_FAILED".to_owned())
}

fn verify(connection: &Connection, session_id: &str, project_id: &str) -> Result<Value, String> {
    if session(connection, session_id, project_id)?.is_none() {
        return Err(format!("SESSION_NOT_FOUND:{session_id}"));
    }
    let mut statement = connection
        .prepare(
            "SELECT sequence,event_type,payload_json,previous_hash,event_hash,created_at \
             FROM session_events WHERE session_id=?1 ORDER BY sequence",
        )
        .map_err(|error| format!("SESSION_VERIFY_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([session_id], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, f64>(5)?,
            ))
        })
        .map_err(|error| format!("SESSION_VERIFY_QUERY_FAILED:{error}"))?;
    let mut reasons = Vec::new();
    let mut previous = ZERO_HASH.to_owned();
    let mut expected_sequence = 1_i64;
    for row in rows {
        let (sequence, event_type, payload, previous_hash, event_hash, created_at) =
            row.map_err(|error| format!("SESSION_VERIFY_ROW_FAILED:{error}"))?;
        if sequence != expected_sequence {
            reasons.push(format!("sequence-gap:{expected_sequence}->{sequence}"));
        }
        if previous_hash != previous {
            reasons.push(format!("previous-hash-mismatch:{sequence}"));
        }
        let payload: Value = serde_json::from_str(&payload)
            .map_err(|_| "SESSION_EVENT_PAYLOAD_INVALID".to_owned())?;
        let material = json!({
            "session_id": session_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at": created_at,
        });
        if sha256_hex(&canonical_bytes(&material)?) != event_hash {
            reasons.push(format!("event-hash-mismatch:{sequence}"));
        }
        previous = event_hash;
        expected_sequence = sequence + 1;
    }
    Ok(json!({
        "ok": reasons.is_empty(),
        "events": expected_sequence - 1,
        "last_hash": previous,
        "reasons": reasons,
    }))
}

fn open_session(
    arguments: &[String],
    project_id: &str,
    state_root: &Path,
    connection: &mut Connection,
) -> Result<Value, String> {
    let requested = option_value(arguments, "--session-id")?.filter(|value| !value.is_empty());
    let metadata_source = option_value(arguments, "--metadata")?.unwrap_or_else(|| "{}".to_owned());
    let metadata = load_json_object(&metadata_source, "SESSION_METADATA")?;

    let (session_id, restored) = if let Some(session_id) = requested {
        if session(connection, &session_id, project_id)?.is_some() {
            (session_id, true)
        } else {
            (session_id, false)
        }
    } else {
        (generated_session_id()?, false)
    };

    if !restored {
        let now = now_seconds()?;
        let metadata_json = serde_json::to_string(&metadata)
            .map_err(|_| "SESSION_METADATA_SERIALIZE_FAILED".to_owned())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| format!("SESSION_CREATE_TRANSACTION_FAILED:{error}"))?;
        transaction
            .execute(
                "INSERT INTO sessions(session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json) \
                 VALUES(?1,?2,'[]','ACTIVE',?3,?3,?4)",
                params![session_id, project_id, now, metadata_json],
            )
            .map_err(|error| format!("SESSION_CREATE_FAILED:{error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("SESSION_CREATE_COMMIT_FAILED:{error}"))?;
    }

    let session = session(connection, &session_id, project_id)?
        .ok_or_else(|| format!("SESSION_NOT_FOUND:{session_id}"))?;
    let verification = verify(connection, &session_id, project_id)?;
    super::native_analytics::record_event(
        &json!({
            "session_id": session_id,
            "repository_hash": project_id,
            "kind": "session-open",
            "success": verification["ok"],
            "continuity_restored": restored,
            "metadata": {"events": verification["events"]},
        }),
        state_root,
    )?;
    Ok(json!({
        "ok": verification["ok"],
        "session": session,
        "continuity_restored": restored,
        "verification": verification,
    }))
}

fn append_session(
    arguments: &[String],
    project_id: &str,
    state_root: &Path,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "session-append", 1)?;
    let event_type = positional_after(arguments, "session-append", 2)?;
    let payload_source = positional_after(arguments, "session-append", 3)?;
    let payload = load_json_object(payload_source, "SESSION_PAYLOAD")?;
    if session(connection, session_id, project_id)?.is_none() {
        return Err(format!("SESSION_NOT_FOUND:{session_id}"));
    }

    let started = Instant::now();
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_APPEND_TRANSACTION_FAILED:{error}"))?;
    let previous = transaction
        .query_row(
            "SELECT sequence,event_hash FROM session_events WHERE session_id=?1 ORDER BY sequence DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_APPEND_PREVIOUS_FAILED:{error}"))?;
    let sequence = previous.as_ref().map_or(1, |row| row.0 + 1);
    let previous_hash = previous.map_or_else(|| ZERO_HASH.to_owned(), |row| row.1);
    let created_at = now_seconds()?;
    let material = json!({
        "session_id": session_id,
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "created_at": created_at,
    });
    let event_hash = sha256_hex(&canonical_bytes(&material)?);
    let payload_json = serde_json::to_string(&material["payload"])
        .map_err(|_| "SESSION_PAYLOAD_SERIALIZE_FAILED".to_owned())?;
    transaction
        .execute(
            "INSERT INTO session_events(session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at) \
             VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![
                session_id,
                sequence,
                event_type,
                payload_json,
                previous_hash,
                event_hash,
                created_at,
            ],
        )
        .map_err(|error| format!("SESSION_APPEND_FAILED:{error}"))?;
    transaction
        .execute(
            "UPDATE sessions SET updated_at=?1 WHERE session_id=?2",
            params![created_at, session_id],
        )
        .map_err(|error| format!("SESSION_UPDATE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_APPEND_COMMIT_FAILED:{error}"))?;
    let wall_time_ms = started.elapsed().as_secs_f64() * 1000.0;

    super::native_analytics::record_event(
        &json!({
            "session_id": session_id,
            "repository_hash": project_id,
            "kind": "session-append",
            "wall_time_ms": wall_time_ms,
            "success": true,
            "metadata": {"event_type": event_type, "sequence": sequence},
        }),
        state_root,
    )?;
    Ok(json!({
        "ok": true,
        "event": {
            "session_id": session_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": material["payload"],
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        },
        "wall_time_ms": wall_time_ms,
    }))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::state_snapshot_contract::project_id_for_root(&project)?;
    let mut connection = initialize_database(&state_root.join("sessions.sqlite3"))?;
    match command.get(1).map(String::as_str) {
        Some("session-open") => open_session(arguments, &project_id, state_root, &mut connection),
        Some("session-append") => {
            append_session(arguments, &project_id, state_root, &mut connection)
        }
        _ => Err("SESSION_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn session_mutation_commands_are_supported() {
        for action in ["session-open", "session-append"] {
            let command = ["run", action]
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            assert!(supports(&command));
        }
    }
}
