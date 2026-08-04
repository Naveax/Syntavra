#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
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
        if root == "session" && matches!(action.as_str(), "open" | "append" | "compact" | "import"))
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_PUBLIC_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_PUBLIC_DATABASE_OPEN_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_PUBLIC_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SESSION_PUBLIC_SYSTEM_CLOCK_INVALID".to_owned())
}

fn generated_id(prefix: &str) -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SESSION_PUBLIC_SYSTEM_CLOCK_INVALID".to_owned())?;
    let material = format!(
        "{prefix}:{}:{}:{:?}",
        duration.as_nanos(),
        std::process::id(),
        std::thread::current().id(),
    );
    Ok(format!(
        "{prefix}-{}",
        &sha256_hex(material.as_bytes())[..32]
    ))
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_PUBLIC_JSON_SERIALIZE_FAILED".to_owned())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let candidate = if arguments[index] == flag {
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
        if let Some(candidate) = candidate {
            value = Some(candidate);
        }
        index += 1;
    }
    Ok(value)
}

fn repeated_option(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut output = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            output.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            output.push(value.to_owned());
        }
        index += 1;
    }
    Ok(output)
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?.map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| format!("{flag}_VALUE_INVALID"))
    })
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    offset: usize,
) -> Result<&'a str, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "session" && window[1] == action)
        .ok_or_else(|| format!("SESSION_{}_ACTION_MISSING", action.to_ascii_uppercase()))?;
    arguments
        .get(index + 1 + offset)
        .map(String::as_str)
        .ok_or_else(|| {
            format!(
                "SESSION_{}_ARGUMENT_MISSING:{offset}",
                action.to_ascii_uppercase()
            )
        })
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
        .map_err(|error| format!("SESSION_PUBLIC_SESSION_QUERY_FAILED:{error}"))?;
    let Some(row) = row else {
        return Ok(None);
    };
    let parents: Value =
        serde_json::from_str(&row.2).map_err(|_| "SESSION_PUBLIC_PARENT_IDS_INVALID".to_owned())?;
    let metadata: Value =
        serde_json::from_str(&row.6).map_err(|_| "SESSION_PUBLIC_METADATA_INVALID".to_owned())?;
    Ok(Some(json!({
        "session_id": row.0,
        "project_id": row.1,
        "parent_ids": parents,
        "state": row.3,
        "created_at": row.4,
        "updated_at": row.5,
        "metadata": metadata,
    })))
}

fn create_session(
    connection: &mut Connection,
    project_id: &str,
    requested_id: Option<&str>,
    parents: &[String],
    metadata: &Value,
) -> Result<Value, String> {
    let session_id =
        requested_id.map_or_else(|| generated_id("sess"), |value| Ok(value.to_owned()))?;
    let mut unique = Vec::new();
    let mut seen = BTreeSet::new();
    for parent in parents {
        if seen.insert(parent.clone()) {
            unique.push(parent.clone());
        }
    }
    for parent in &unique {
        if session(connection, parent, project_id)?.is_none() {
            return Err(format!("INVALID_PARENT_SESSION:{parent}"));
        }
    }
    let created_at = now()?;
    let parent_json = serde_json::to_string(&unique)
        .map_err(|_| "SESSION_PUBLIC_PARENT_IDS_SERIALIZE_FAILED".to_owned())?;
    let metadata_json = serde_json::to_string(metadata)
        .map_err(|_| "SESSION_PUBLIC_METADATA_SERIALIZE_FAILED".to_owned())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_PUBLIC_CREATE_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO sessions(session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json) \
             VALUES(?1,?2,?3,'ACTIVE',?4,?4,?5)",
            params![session_id, project_id, parent_json, created_at, metadata_json],
        )
        .map_err(|error| format!("SESSION_PUBLIC_CREATE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_PUBLIC_CREATE_COMMIT_FAILED:{error}"))?;
    session(connection, &session_id, project_id)?
        .ok_or_else(|| format!("SESSION_NOT_FOUND:{session_id}"))
}

fn append_event(
    connection: &mut Connection,
    session_id: &str,
    project_id: &str,
    event_type: &str,
    payload: &Value,
) -> Result<Value, String> {
    if session(connection, session_id, project_id)?.is_none() {
        return Err(format!("SESSION_NOT_FOUND:{session_id}"));
    }
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_PUBLIC_APPEND_TRANSACTION_FAILED:{error}"))?;
    let previous = transaction
        .query_row(
            "SELECT sequence,event_hash FROM session_events WHERE session_id=?1 ORDER BY sequence DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_PUBLIC_APPEND_PREVIOUS_FAILED:{error}"))?;
    let sequence = previous.as_ref().map_or(1, |row| row.0 + 1);
    let previous_hash = previous.map_or_else(|| ZERO_HASH.to_owned(), |row| row.1);
    let created_at = now()?;
    let material = json!({
        "session_id": session_id,
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "created_at": created_at,
    });
    let event_hash = sha256_hex(&canonical_bytes(&material)?);
    let payload_json = serde_json::to_string(payload)
        .map_err(|_| "SESSION_PUBLIC_PAYLOAD_SERIALIZE_FAILED".to_owned())?;
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
        .map_err(|error| format!("SESSION_PUBLIC_APPEND_FAILED:{error}"))?;
    transaction
        .execute(
            "UPDATE sessions SET updated_at=?1 WHERE session_id=?2",
            params![created_at, session_id],
        )
        .map_err(|error| format!("SESSION_PUBLIC_UPDATE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_PUBLIC_APPEND_COMMIT_FAILED:{error}"))?;
    Ok(json!({
        "session_id": session_id,
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "event_hash": event_hash,
        "created_at": created_at,
    }))
}

fn events(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<Vec<Event>, String> {
    if session(connection, session_id, project_id)?.is_none() {
        return Err(format!("SESSION_NOT_FOUND:{session_id}"));
    }
    let mut statement = connection
        .prepare(
            "SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at \
             FROM session_events WHERE session_id=?1 ORDER BY sequence",
        )
        .map_err(|error| format!("SESSION_PUBLIC_EVENT_PREPARE_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_PUBLIC_EVENT_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("SESSION_PUBLIC_EVENT_ROW_FAILED:{error}"))?;
        output.push(Event {
            session_id: row.0,
            sequence: row.1,
            event_type: row.2,
            payload: serde_json::from_str(&row.3)
                .map_err(|_| "SESSION_PUBLIC_EVENT_PAYLOAD_INVALID".to_owned())?,
            previous_hash: row.4,
            event_hash: row.5,
            created_at: row.6,
        });
    }
    Ok(output)
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
            values
                .iter()
                .map(python_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| format!(
                    "'{}': {}",
                    key.replace('\'', "\\'"),
                    python_repr(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::String(value) => format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'")),
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
                    facts.push(format!(
                        "#{} {key}={}",
                        event.sequence,
                        python_string(value).chars().take(400).collect::<String>()
                    ));
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

fn compact(
    connection: &mut Connection,
    session_id: &str,
    project_id: &str,
    leaf_size: i64,
    fanout: i64,
    force: bool,
) -> Result<Option<String>, String> {
    let rows = events(connection, session_id, project_id)?;
    if rows.is_empty() {
        return Ok(None);
    }
    if !force {
        let existing = connection
            .query_row(
                "SELECT summary_id,source_end FROM session_summaries \
                 WHERE session_id=?1 AND invalidated_at IS NULL ORDER BY source_end DESC LIMIT 1",
                [session_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)),
            )
            .optional()
            .map_err(|error| format!("SESSION_PUBLIC_SUMMARY_QUERY_FAILED:{error}"))?;
        if let Some((summary_id, source_end)) = existing {
            if source_end >= rows.last().map_or(0, |event| event.sequence) {
                return Ok(Some(summary_id));
            }
        }
    }
    let leaf_size = usize::try_from(leaf_size.max(1))
        .map_err(|_| "SESSION_PUBLIC_LEAF_SIZE_OVERFLOW".to_owned())?;
    let fanout =
        usize::try_from(fanout.max(2)).map_err(|_| "SESSION_PUBLIC_FANOUT_OVERFLOW".to_owned())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_PUBLIC_COMPACT_TRANSACTION_FAILED:{error}"))?;
    let mut level_nodes = Vec::<(String, i64, i64, String)>::new();
    for group in rows.chunks(leaf_size) {
        let source_hash = sha256_hex(&canonical_bytes(&Value::Array(
            group
                .iter()
                .map(|event| Value::String(event.event_hash.clone()))
                .collect(),
        ))?);
        let start = group.first().map_or(0, |event| event.sequence);
        let end = group.last().map_or(0, |event| event.sequence);
        let material = json!({
            "session": session_id,
            "start": start,
            "end": end,
            "hash": source_hash,
            "level": 0,
        });
        let summary_id = format!("sum-{}", &sha256_hex(&canonical_bytes(&material)?)[..32]);
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
                    now()?,
                ],
            )
            .map_err(|error| format!("SESSION_PUBLIC_SUMMARY_INSERT_FAILED:{error}"))?;
        level_nodes.push((summary_id, start, end, source_hash));
    }
    let mut level = 1_i64;
    while level_nodes.len() > 1 {
        let mut next_nodes = Vec::new();
        for group in level_nodes.chunks(fanout) {
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
                            format!("SESSION_PUBLIC_SUMMARY_CONTENT_FAILED:{error}")
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
            let summary_id = format!("sum-{}", &sha256_hex(&canonical_bytes(&material)?)[..32]);
            let children_json = serde_json::to_string(&material["children"])
                .map_err(|_| "SESSION_PUBLIC_SUMMARY_CHILDREN_INVALID".to_owned())?;
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
                        now()?,
                    ],
                )
                .map_err(|error| format!("SESSION_PUBLIC_SUMMARY_INSERT_FAILED:{error}"))?;
            next_nodes.push((summary_id, start, end, source_hash));
        }
        level_nodes = next_nodes;
        level += 1;
    }
    let root = level_nodes.first().map(|row| row.0.clone());
    transaction
        .commit()
        .map_err(|error| format!("SESSION_PUBLIC_COMPACT_COMMIT_FAILED:{error}"))?;
    Ok(root)
}

fn open(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let requested = option_value(arguments, "--session-id")?.filter(|value| !value.is_empty());
    let parents = repeated_option(arguments, "--parent")?;
    let task = option_value(arguments, "--task")?.filter(|value| !value.is_empty());
    let metadata = task.map_or_else(|| json!({}), |value| json!({"task": value}));
    create_session(
        connection,
        project_id,
        requested.as_deref(),
        &parents,
        &metadata,
    )
}

fn append(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "append", 1)?;
    let event_type = positional_after(arguments, "append", 2)?;
    let payload_text = positional_after(arguments, "append", 3)?;
    let payload: Value = serde_json::from_str(payload_text)
        .map_err(|_| "SESSION_PUBLIC_PAYLOAD_JSON_INVALID".to_owned())?;
    append_event(connection, session_id, project_id, event_type, &payload)
}

fn compact_command(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "compact", 1)?;
    let root = compact(
        connection,
        session_id,
        project_id,
        integer_option(arguments, "--leaf-size", 32)?,
        integer_option(arguments, "--fanout", 8)?,
        flag(arguments, "--force"),
    )?;
    Ok(json!({"session_id": session_id, "root_summary_id": root}))
}

fn quarantine(
    connection: &mut Connection,
    session_id: &str,
    object_id: &str,
    payload: &Value,
) -> Result<(), String> {
    let payload_json = serde_json::to_string(payload)
        .map_err(|_| "SESSION_PUBLIC_QUARANTINE_PAYLOAD_INVALID".to_owned())?;
    connection
        .execute(
            "INSERT INTO session_quarantine(\
             session_id,object_type,object_id,reason,payload_json,created_at) \
             VALUES(?1,'event',?2,'import-hash-changed',?3,?4)",
            params![session_id, object_id, payload_json, now()?],
        )
        .map_err(|error| format!("SESSION_PUBLIC_QUARANTINE_INSERT_FAILED:{error}"))?;
    Ok(())
}

fn import(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let input = option_value(arguments, "--input")?
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "SESSION_IMPORT_INPUT_REQUIRED".to_owned())?;
    let explicit_id = option_value(arguments, "--session-id")?.filter(|value| !value.is_empty());
    let bytes = fs::read(&input).map_err(|error| format!("SESSION_IMPORT_READ_FAILED:{error}"))?;
    let mut value: Value =
        serde_json::from_slice(&bytes).map_err(|_| "SESSION_IMPORT_JSON_INVALID".to_owned())?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| "SESSION_IMPORT_OBJECT_REQUIRED".to_owned())?;
    let saved_hash = object.remove("export_hash");
    let calculated = sha256_hex(&canonical_bytes(&value)?);
    if saved_hash.as_ref().and_then(Value::as_str) != Some(calculated.as_str()) {
        return Err("SESSION_EXPORT_HASH_MISMATCH".to_owned());
    }
    let source = value
        .get("session")
        .and_then(Value::as_object)
        .ok_or_else(|| "SESSION_IMPORT_SOURCE_INVALID".to_owned())?;
    let source_id = source
        .get("session_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "SESSION_IMPORT_SOURCE_ID_INVALID".to_owned())?;
    let mut metadata = Map::new();
    metadata.insert(
        "imported_from".to_owned(),
        Value::String(source_id.to_owned()),
    );
    if let Some(source_metadata) = source.get("metadata").and_then(Value::as_object) {
        for (key, value) in source_metadata {
            metadata.insert(key.clone(), value.clone());
        }
    }
    let created = create_session(
        connection,
        project_id,
        explicit_id.as_deref(),
        &[],
        &Value::Object(metadata),
    )?;
    let new_id = created["session_id"]
        .as_str()
        .ok_or_else(|| "SESSION_IMPORT_CREATED_ID_INVALID".to_owned())?
        .to_owned();
    let imported_events = value
        .get("events")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for row in imported_events {
        let event_type = row
            .get("event_type")
            .and_then(Value::as_str)
            .ok_or_else(|| "SESSION_IMPORT_EVENT_TYPE_INVALID".to_owned())?;
        let payload = row
            .get("payload")
            .ok_or_else(|| "SESSION_IMPORT_EVENT_PAYLOAD_MISSING".to_owned())?;
        let imported = append_event(connection, &new_id, project_id, event_type, payload)?;
        if explicit_id.is_none() && imported.get("event_hash") != row.get("event_hash") {
            let sequence = row
                .get("sequence")
                .map(Value::to_string)
                .unwrap_or_else(|| "None".to_owned());
            quarantine(connection, &new_id, &sequence, &row)?;
        }
    }
    Ok(created)
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
        [root, action] if root == "session" && action == "open" => {
            open(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "append" => {
            append(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "compact" => {
            compact_command(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "import" => {
            import(arguments, &project_id, &mut connection)
        }
        _ => Err("SESSION_PUBLIC_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn public_session_commands_are_supported() {
        for action in ["open", "append", "compact", "import"] {
            assert!(supports(&["session".to_owned(), action.to_owned()]));
        }
    }
}
