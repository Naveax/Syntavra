#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Debug, Clone)]
struct Event {
    session_id: String,
    sequence: i64,
    event_type: String,
    payload: Value,
    previous_hash: String,
    event_hash: String,
    created_at: f64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "session" && matches!(action.as_str(), "checkpoint" | "export"))
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_ARCHIVE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_ARCHIVE_DATABASE_OPEN_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_ARCHIVE_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SESSION_ARCHIVE_SYSTEM_CLOCK_INVALID".to_owned())
}

fn now_nanos() -> Result<u128, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .map_err(|_| "SESSION_ARCHIVE_SYSTEM_CLOCK_INVALID".to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_ARCHIVE_JSON_SERIALIZE_FAILED".to_owned())
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
        .ok_or_else(|| format!("SESSION_{}_ID_MISSING", action.to_ascii_uppercase()))
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let current = if arguments[index] == flag {
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
        if let Some(current) = current {
            if found.replace(current).is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
        }
        index += 1;
    }
    Ok(found)
}

fn session(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<Value, String> {
    let row = connection
        .query_row(
            "SELECT session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json \
             FROM sessions WHERE session_id=?1 AND project_id=?2",
            [session_id, project_id],
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
        .map_err(|error| format!("SESSION_ARCHIVE_SESSION_QUERY_FAILED:{error}"))?
        .ok_or_else(|| format!("SESSION_NOT_FOUND:{session_id}"))?;
    let parent_ids: Value = serde_json::from_str(&row.2)
        .map_err(|_| "SESSION_ARCHIVE_PARENT_IDS_INVALID".to_owned())?;
    if !parent_ids.is_array() {
        return Err("SESSION_ARCHIVE_PARENT_IDS_INVALID".to_owned());
    }
    let metadata: Value = serde_json::from_str(&row.6)
        .map_err(|_| "SESSION_ARCHIVE_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "session_id": row.0,
        "project_id": row.1,
        "parent_ids": parent_ids,
        "state": row.3,
        "created_at": row.4,
        "updated_at": row.5,
        "metadata": metadata,
    }))
}

fn events(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<Vec<Event>, String> {
    session(connection, session_id, project_id)?;
    let mut statement = connection
        .prepare(
            "SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at \
             FROM session_events WHERE session_id=?1 ORDER BY sequence",
        )
        .map_err(|error| format!("SESSION_ARCHIVE_EVENT_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([session_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, f64>(6)?,
            ))
        })
        .map_err(|error| format!("SESSION_ARCHIVE_EVENT_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("SESSION_ARCHIVE_EVENT_ROW_FAILED:{error}"))?;
        output.push(Event {
            session_id: row.0,
            sequence: row.1,
            event_type: row.2,
            payload: serde_json::from_str(&row.3)
                .map_err(|_| "SESSION_ARCHIVE_EVENT_PAYLOAD_INVALID".to_owned())?,
            previous_hash: row.4,
            event_hash: row.5,
            created_at: row.6,
        });
    }
    Ok(output)
}

fn event_json(event: &Event) -> Value {
    json!({
        "session_id": event.session_id,
        "sequence": event.sequence,
        "event_type": event.event_type,
        "payload": event.payload,
        "previous_hash": event.previous_hash,
        "event_hash": event.event_hash,
        "created_at": event.created_at,
    })
}

fn verify_rows(rows: &[Event]) -> Result<Value, String> {
    let mut reasons = Vec::new();
    let mut previous = ZERO_HASH.to_owned();
    let mut expected_sequence = 1_i64;
    for event in rows {
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
            "payload": event.payload,
            "previous_hash": event.previous_hash,
            "created_at": event.created_at,
        });
        if sha256_hex(&canonical_bytes(&material)?) != event.event_hash {
            reasons.push(format!("event-hash-mismatch:{}", event.sequence));
        }
        previous.clone_from(&event.event_hash);
        expected_sequence = event.sequence + 1;
    }
    Ok(json!({
        "ok": reasons.is_empty(),
        "events": expected_sequence - 1,
        "last_hash": previous,
        "reasons": reasons,
    }))
}

fn python_string(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(value) => {
            if *value {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        Value::Number(value) => value.to_string(),
        Value::String(value) => value.clone(),
        Value::Array(values) => format!(
            "[{}]",
            values.iter().map(python_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(values) => {
            let rows = values
                .iter()
                .map(|(key, value)| {
                    format!("'{}': {}", key.replace('\'', "\\'"), python_repr(value))
                })
                .collect::<Vec<_>>();
            format!("{{{}}}", rows.join(", "))
        }
    }
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::String(value) => format!(
            "'{}'",
            value.replace('\\', "\\\\").replace('\'', "\\'")
        ),
        _ => python_string(value),
    }
}

fn deterministic_summary(rows: &[Event]) -> String {
    let mut counts = BTreeMap::<String, usize>::new();
    let mut facts = Vec::new();
    for event in rows {
        *counts.entry(event.event_type.clone()).or_default() += 1;
        if let Some(payload) = event.payload.as_object() {
            for key in [
                "task", "decision", "error", "result", "path", "command", "claim",
            ] {
                if facts.len() >= 20 {
                    break;
                }
                if let Some(value) = payload.get(key) {
                    let text = python_string(value).chars().take(400).collect::<String>();
                    facts.push(format!("#{} {key}={text}", event.sequence));
                }
            }
        }
    }
    let counts = counts
        .into_iter()
        .map(|(name, count)| format!("{name}={count}"))
        .collect::<Vec<_>>()
        .join(", ");
    if facts.is_empty() {
        format!("Events {counts}")
    } else {
        format!("Events {counts}\n{}", facts.join("\n"))
    }
}

fn compact_force(
    connection: &mut Connection,
    session_id: &str,
    rows: &[Event],
) -> Result<Option<String>, String> {
    if rows.is_empty() {
        return Ok(None);
    }
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_ARCHIVE_COMPACT_TRANSACTION_FAILED:{error}"))?;
    let mut level_nodes = Vec::<(String, i64, i64, String)>::new();
    for group in rows.chunks(32) {
        let hashes = group
            .iter()
            .map(|event| Value::String(event.event_hash.clone()))
            .collect::<Vec<_>>();
        let source_hash = sha256_hex(&canonical_bytes(&Value::Array(hashes))?);
        let start = group.first().map_or(0, |event| event.sequence);
        let end = group.last().map_or(0, |event| event.sequence);
        let material = json!({
            "session": session_id,
            "start": start,
            "end": end,
            "hash": source_hash,
            "level": 0,
        });
        let summary_id = format!(
            "sum-{}",
            &sha256_hex(&canonical_bytes(&material)?)[..32]
        );
        transaction
            .execute(
                "INSERT OR REPLACE INTO session_summaries(\
                 summary_id,session_id,content,source_start,source_end,child_ids_json,source_hash,\
                 order_level,created_at,invalidated_at) VALUES(?1,?2,?3,?4,?5,'[]',?6,0,?7,NULL)",
                params![
                    summary_id,
                    session_id,
                    deterministic_summary(group),
                    start,
                    end,
                    source_hash,
                    now_seconds()?,
                ],
            )
            .map_err(|error| format!("SESSION_ARCHIVE_SUMMARY_INSERT_FAILED:{error}"))?;
        level_nodes.push((summary_id, start, end, source_hash));
    }
    let mut level = 1_i64;
    while level_nodes.len() > 1 {
        let mut next_nodes = Vec::new();
        for group in level_nodes.chunks(8) {
            let child_ids = group.iter().map(|row| row.0.clone()).collect::<Vec<_>>();
            let child_hashes = group
                .iter()
                .map(|row| Value::String(row.3.clone()))
                .collect::<Vec<_>>();
            let mut contents = Vec::new();
            for child_id in &child_ids {
                contents.push(
                    transaction
                        .query_row(
                            "SELECT content FROM session_summaries WHERE summary_id=?1",
                            [child_id],
                            |row| row.get::<_, String>(0),
                        )
                        .map_err(|error| {
                            format!("SESSION_ARCHIVE_SUMMARY_CONTENT_FAILED:{error}")
                        })?,
                );
            }
            let start = group.first().map_or(0, |row| row.1);
            let end = group.last().map_or(0, |row| row.2);
            let content = format!(
                "Summary level {level}; coverage {start}-{end}\n{}",
                contents.join("\n---\n")
            );
            let source_hash = sha256_hex(&canonical_bytes(&Value::Array(child_hashes))?);
            let material = json!({
                "session": session_id,
                "children": child_ids,
                "hash": source_hash,
                "level": level,
            });
            let summary_id = format!(
                "sum-{}",
                &sha256_hex(&canonical_bytes(&material)?)[..32]
            );
            let children_json = serde_json::to_string(&material["children"])
                .map_err(|_| "SESSION_ARCHIVE_SUMMARY_CHILDREN_INVALID".to_owned())?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO session_summaries(\
                     summary_id,session_id,content,source_start,source_end,child_ids_json,source_hash,\
                     order_level,created_at,invalidated_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,NULL)",
                    params![
                        summary_id,
                        session_id,
                        content,
                        start,
                        end,
                        children_json,
                        source_hash,
                        level,
                        now_seconds()?,
                    ],
                )
                .map_err(|error| format!("SESSION_ARCHIVE_SUMMARY_INSERT_FAILED:{error}"))?;
            next_nodes.push((summary_id, start, end, source_hash));
        }
        level_nodes = next_nodes;
        level += 1;
    }
    let root = level_nodes.first().map(|row| row.0.clone());
    transaction
        .commit()
        .map_err(|error| format!("SESSION_ARCHIVE_COMPACT_COMMIT_FAILED:{error}"))?;
    Ok(root)
}

fn checkpoint_id(session_id: &str, event_hash: &str) -> Result<String, String> {
    let material = format!(
        "{session_id}:{event_hash}:{}:{}:{:?}",
        now_nanos()?,
        std::process::id(),
        std::thread::current().id(),
    );
    Ok(format!(
        "cp-{}",
        &sha256_hex(material.as_bytes())[..32]
    ))
}

fn checkpoint(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "session", "checkpoint")?;
    let rows = events(connection, session_id, project_id)?;
    let verification = verify_rows(&rows)?;
    if verification.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("SESSION_HISTORY_VERIFICATION_FAILED".to_owned());
    }
    let root = compact_force(connection, session_id, &rows)?;
    let through_sequence = verification
        .get("events")
        .and_then(Value::as_i64)
        .ok_or_else(|| "SESSION_ARCHIVE_EVENT_COUNT_INVALID".to_owned())?;
    let event_hash = verification
        .get("last_hash")
        .and_then(Value::as_str)
        .ok_or_else(|| "SESSION_ARCHIVE_LAST_HASH_INVALID".to_owned())?
        .to_owned();
    let label = option_value(arguments, "--label")?.filter(|value| !value.is_empty());
    let metadata = label.map_or_else(|| json!({}), |value| json!({"label": value}));
    let metadata_json = serde_json::to_string(&metadata)
        .map_err(|_| "SESSION_ARCHIVE_CHECKPOINT_METADATA_INVALID".to_owned())?;
    let checkpoint_id = checkpoint_id(session_id, &event_hash)?;
    let created_at = now_seconds()?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO session_checkpoints(\
             checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at)\
             VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![
                checkpoint_id,
                session_id,
                through_sequence,
                root,
                event_hash,
                metadata_json,
                created_at,
            ],
        )
        .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_INSERT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_COMMIT_FAILED:{error}"))?;
    Ok(json!({
        "checkpoint_id": checkpoint_id,
        "session_id": session_id,
        "through_sequence": through_sequence,
        "root_summary_id": root,
        "event_hash": event_hash,
        "created_at": created_at,
        "metadata": metadata,
    }))
}

fn checkpoints(connection: &Connection, session_id: &str) -> Result<Vec<Value>, String> {
    let mut statement = connection
        .prepare(
            "SELECT checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at \
             FROM session_checkpoints WHERE session_id=?1 ORDER BY created_at",
        )
        .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_LIST_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([session_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, f64>(6)?,
            ))
        })
        .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_LIST_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row
            .map_err(|error| format!("SESSION_ARCHIVE_CHECKPOINT_LIST_ROW_FAILED:{error}"))?;
        let metadata: Value = serde_json::from_str(&row.5)
            .map_err(|_| "SESSION_ARCHIVE_CHECKPOINT_METADATA_INVALID".to_owned())?;
        output.push(json!({
            "checkpoint_id": row.0,
            "session_id": row.1,
            "through_sequence": row.2,
            "root_summary_id": row.3,
            "event_hash": row.4,
            "metadata_json": row.5,
            "created_at": row.6,
            "metadata": metadata,
        }));
    }
    Ok(output)
}

fn output_path(arguments: &[String]) -> Result<PathBuf, String> {
    option_value(arguments, "--output")?
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| "SESSION_EXPORT_OUTPUT_REQUIRED".to_owned())
}

fn temp_file(path: &Path) -> Result<(PathBuf, File), String> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .map_err(|error| format!("SESSION_EXPORT_DIRECTORY_CREATE_FAILED:{error}"))?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "SESSION_EXPORT_FILE_NAME_INVALID".to_owned())?;
    for attempt in 0_u32..100 {
        let candidate = parent.join(format!(
            ".{name}.{}.{}.{}",
            std::process::id(),
            now_nanos()?,
            attempt,
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => return Ok((candidate, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("SESSION_EXPORT_TEMP_CREATE_FAILED:{error}")),
        }
    }
    Err("SESSION_EXPORT_TEMP_NAME_EXHAUSTED".to_owned())
}

#[cfg(unix)]
fn set_private_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|error| format!("SESSION_EXPORT_PERMISSION_FAILED:{error}"))
}

#[cfg(not(unix))]
fn set_private_permissions(_path: &Path) -> Result<(), String> {
    Ok(())
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    let mut bytes = canonical_bytes(value)?;
    bytes.push(b'\n');
    let (temporary, mut file) = temp_file(path)?;
    let result = (|| {
        file.write_all(&bytes)
            .map_err(|error| format!("SESSION_EXPORT_WRITE_FAILED:{error}"))?;
        file.flush()
            .map_err(|error| format!("SESSION_EXPORT_FLUSH_FAILED:{error}"))?;
        file.sync_all()
            .map_err(|error| format!("SESSION_EXPORT_SYNC_FAILED:{error}"))?;
        drop(file);
        set_private_permissions(&temporary)?;
        #[cfg(windows)]
        if path.exists() {
            fs::remove_file(path)
                .map_err(|error| format!("SESSION_EXPORT_REPLACE_REMOVE_FAILED:{error}"))?;
        }
        fs::rename(&temporary, path)
            .map_err(|error| format!("SESSION_EXPORT_REPLACE_FAILED:{error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn export(
    arguments: &[String],
    project_id: &str,
    connection: &Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "session", "export")?;
    let path = output_path(arguments)?;
    let session = session(connection, session_id, project_id)?;
    let rows = events(connection, session_id, project_id)?;
    let verification = verify_rows(&rows)?;
    let event_values = rows.iter().map(event_json).collect::<Vec<_>>();
    let checkpoints = checkpoints(connection, session_id)?;
    let mut payload = json!({
        "schema_version": 1,
        "project_id": project_id,
        "session": session,
        "events": event_values,
        "checkpoints": checkpoints,
        "verification": verification,
    });
    let export_hash = sha256_hex(&canonical_bytes(&payload)?);
    payload
        .as_object_mut()
        .ok_or_else(|| "SESSION_EXPORT_PAYLOAD_INVALID".to_owned())?
        .insert("export_hash".to_owned(), Value::String(export_hash.clone()));
    atomic_write_json(&path, &payload)?;
    Ok(json!({
        "path": path.to_string_lossy(),
        "events": rows.len(),
        "hash": export_hash,
    }))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::super::state_snapshot_contract::project_id_for_root(&project)?;
    let mut connection = initialize(&state_root.join("sessions.sqlite3"))?;
    match command {
        [root, action] if root == "session" && action == "checkpoint" => {
            checkpoint(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "export" => {
            export(arguments, &project_id, &connection)
        }
        _ => Err("SESSION_ARCHIVE_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn session_archive_commands_are_supported() {
        assert!(supports(&[
            "session".to_owned(),
            "checkpoint".to_owned(),
        ]));
        assert!(supports(&["session".to_owned(), "export".to_owned()]));
    }
}
