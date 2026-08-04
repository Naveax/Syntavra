#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Value};
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
        if root == "session" && matches!(action.as_str(), "fork" | "merge" | "close"))
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_LIFECYCLE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_LIFECYCLE_DATABASE_OPEN_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_LIFECYCLE_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SESSION_LIFECYCLE_SYSTEM_CLOCK_INVALID".to_owned())
}

fn now_nanos() -> Result<u128, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .map_err(|_| "SESSION_LIFECYCLE_SYSTEM_CLOCK_INVALID".to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_LIFECYCLE_JSON_SERIALIZE_FAILED".to_owned())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            found = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

fn positional_after<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "session" && window[1] == action)
        .map(|window| window[2].as_str())
        .ok_or_else(|| format!("SESSION_{}_ID_MISSING", action.to_ascii_uppercase()))
}

fn merge_parents(arguments: &[String]) -> Result<Vec<String>, String> {
    let start = arguments
        .windows(2)
        .position(|window| window[0] == "session" && window[1] == "merge")
        .map(|index| index + 2)
        .ok_or_else(|| "SESSION_MERGE_IDS_MISSING".to_owned())?;
    let mut parents = Vec::new();
    let mut index = start;
    while index < arguments.len() {
        if arguments[index] == "--label" {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--label=") {
            index += 1;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        if !parents.contains(&arguments[index]) {
            parents.push(arguments[index].clone());
        }
        index += 1;
    }
    if parents.len() < 2 {
        return Err("SESSION_MERGE_REQUIRES_TWO_SESSIONS".to_owned());
    }
    Ok(parents)
}

fn session(connection: &Connection, session_id: &str, project_id: &str) -> Result<Value, String> {
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
        .map_err(|error| format!("SESSION_LIFECYCLE_SESSION_QUERY_FAILED:{error}"))?
        .ok_or_else(|| format!("SESSION_NOT_FOUND:{session_id}"))?;
    let parent_ids: Value = serde_json::from_str(&row.2)
        .map_err(|_| "SESSION_LIFECYCLE_PARENT_IDS_INVALID".to_owned())?;
    let metadata: Value = serde_json::from_str(&row.6)
        .map_err(|_| "SESSION_LIFECYCLE_METADATA_INVALID".to_owned())?;
    if !parent_ids.is_array() || !metadata.is_object() {
        return Err("SESSION_LIFECYCLE_SESSION_JSON_INVALID".to_owned());
    }
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
        .map_err(|error| format!("SESSION_LIFECYCLE_EVENT_PREPARE_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_LIFECYCLE_EVENT_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("SESSION_LIFECYCLE_EVENT_ROW_FAILED:{error}"))?;
        output.push(Event {
            session_id: row.0,
            sequence: row.1,
            event_type: row.2,
            payload: serde_json::from_str(&row.3)
                .map_err(|_| "SESSION_LIFECYCLE_EVENT_PAYLOAD_INVALID".to_owned())?,
            previous_hash: row.4,
            event_hash: row.5,
            created_at: row.6,
        });
    }
    Ok(output)
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
        Value::Bool(value) => if *value { "True" } else { "False" }.to_owned(),
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
        .map_err(|error| format!("SESSION_LIFECYCLE_COMPACT_TRANSACTION_FAILED:{error}"))?;
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
                    now_seconds()?,
                ],
            )
            .map_err(|error| format!("SESSION_LIFECYCLE_SUMMARY_INSERT_FAILED:{error}"))?;
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
                            format!("SESSION_LIFECYCLE_SUMMARY_CONTENT_FAILED:{error}")
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
                        serde_json::to_string(&material["children"])
                            .map_err(|_| "SESSION_LIFECYCLE_SUMMARY_CHILDREN_INVALID".to_owned())?,
                        source_hash,
                        level,
                        now_seconds()?,
                    ],
                )
                .map_err(|error| format!("SESSION_LIFECYCLE_SUMMARY_INSERT_FAILED:{error}"))?;
            next_nodes.push((summary_id, start, end, source_hash));
        }
        level_nodes = next_nodes;
        level += 1;
    }
    let root = level_nodes.first().map(|row| row.0.clone());
    transaction
        .commit()
        .map_err(|error| format!("SESSION_LIFECYCLE_COMPACT_COMMIT_FAILED:{error}"))?;
    Ok(root)
}

fn generated_id(prefix: &str, context: &str) -> Result<String, String> {
    let material = format!(
        "{prefix}:{context}:{}:{}:{:?}",
        now_nanos()?,
        std::process::id(),
        std::thread::current().id(),
    );
    Ok(format!(
        "{prefix}-{}",
        &sha256_hex(material.as_bytes())[..32]
    ))
}

fn checkpoint_with_metadata(
    connection: &mut Connection,
    session_id: &str,
    project_id: &str,
    metadata: &Value,
) -> Result<Value, String> {
    let rows = events(connection, session_id, project_id)?;
    let verification = verify_rows(&rows)?;
    if verification.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("SESSION_HISTORY_VERIFICATION_FAILED".to_owned());
    }
    let root = compact_force(connection, session_id, &rows)?;
    let through_sequence = verification["events"]
        .as_i64()
        .ok_or_else(|| "SESSION_LIFECYCLE_EVENT_COUNT_INVALID".to_owned())?;
    let event_hash = verification["last_hash"]
        .as_str()
        .ok_or_else(|| "SESSION_LIFECYCLE_LAST_HASH_INVALID".to_owned())?
        .to_owned();
    let checkpoint_id = generated_id("cp", &format!("{session_id}:{event_hash}"))?;
    let created_at = now_seconds()?;
    let metadata_json = serde_json::to_string(metadata)
        .map_err(|_| "SESSION_LIFECYCLE_CHECKPOINT_METADATA_INVALID".to_owned())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_LIFECYCLE_CHECKPOINT_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO session_checkpoints(\
             checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at) \
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
        .map_err(|error| format!("SESSION_LIFECYCLE_CHECKPOINT_INSERT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_LIFECYCLE_CHECKPOINT_COMMIT_FAILED:{error}"))?;
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

fn create_session(
    connection: &mut Connection,
    project_id: &str,
    parents: &[String],
    metadata: &Value,
) -> Result<Value, String> {
    for parent in parents {
        session(connection, parent, project_id)?;
    }
    let session_id = generated_id("sess", project_id)?;
    let now = now_seconds()?;
    let parent_ids_json = serde_json::to_string(parents)
        .map_err(|_| "SESSION_LIFECYCLE_PARENT_IDS_INVALID".to_owned())?;
    let metadata_json = serde_json::to_string(metadata)
        .map_err(|_| "SESSION_LIFECYCLE_METADATA_INVALID".to_owned())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_LIFECYCLE_CREATE_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO sessions(\
             session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json) \
             VALUES(?1,?2,?3,'ACTIVE',?4,?4,?5)",
            params![session_id, project_id, parent_ids_json, now, metadata_json],
        )
        .map_err(|error| format!("SESSION_LIFECYCLE_CREATE_INSERT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_LIFECYCLE_CREATE_COMMIT_FAILED:{error}"))?;
    session(connection, &session_id, project_id)
}

fn append_event(
    connection: &mut Connection,
    session_id: &str,
    event_type: &str,
    payload: &Value,
) -> Result<Value, String> {
    let previous = connection
        .query_row(
            "SELECT sequence,event_hash FROM session_events WHERE session_id=?1 \
             ORDER BY sequence DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_LIFECYCLE_PREVIOUS_EVENT_QUERY_FAILED:{error}"))?;
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
    let payload_json = serde_json::to_string(payload)
        .map_err(|_| "SESSION_LIFECYCLE_EVENT_PAYLOAD_INVALID".to_owned())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_LIFECYCLE_APPEND_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO session_events(\
             session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at) \
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
        .map_err(|error| format!("SESSION_LIFECYCLE_APPEND_INSERT_FAILED:{error}"))?;
    transaction
        .execute(
            "UPDATE sessions SET updated_at=?1 WHERE session_id=?2",
            params![created_at, session_id],
        )
        .map_err(|error| format!("SESSION_LIFECYCLE_APPEND_UPDATE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_LIFECYCLE_APPEND_COMMIT_FAILED:{error}"))?;
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

fn label_metadata(arguments: &[String]) -> Result<Value, String> {
    Ok(option_value(arguments, "--label")?
        .filter(|value| !value.is_empty())
        .map_or_else(|| json!({}), |value| json!({"label": value})))
}

fn fork(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let parent = positional_after(arguments, "fork")?.to_owned();
    let checkpoint = checkpoint_with_metadata(
        connection,
        &parent,
        project_id,
        &json!({"reason": "fork-source"}),
    )?;
    let checkpoint_id = checkpoint["checkpoint_id"]
        .as_str()
        .ok_or_else(|| "SESSION_LIFECYCLE_CHECKPOINT_ID_INVALID".to_owned())?
        .to_owned();
    let mut metadata = label_metadata(arguments)?;
    metadata
        .as_object_mut()
        .ok_or_else(|| "SESSION_LIFECYCLE_METADATA_INVALID".to_owned())?
        .insert(
            "fork_checkpoint".to_owned(),
            Value::String(checkpoint_id.clone()),
        );
    let child = create_session(connection, project_id, &[parent.clone()], &metadata)?;
    let child_id = child["session_id"]
        .as_str()
        .ok_or_else(|| "SESSION_LIFECYCLE_CHILD_ID_INVALID".to_owned())?
        .to_owned();
    append_event(
        connection,
        &child_id,
        "session-fork",
        &json!({
            "parent_session": parent,
            "checkpoint": checkpoint_id,
            "through_sequence": checkpoint["through_sequence"],
        }),
    )?;
    session(connection, &child_id, project_id)
}

fn merge(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let parents = merge_parents(arguments)?;
    let mut checkpoint_ids = Vec::new();
    for parent in &parents {
        let checkpoint = checkpoint_with_metadata(
            connection,
            parent,
            project_id,
            &json!({"reason": "merge-source"}),
        )?;
        checkpoint_ids.push(
            checkpoint["checkpoint_id"]
                .as_str()
                .ok_or_else(|| "SESSION_LIFECYCLE_CHECKPOINT_ID_INVALID".to_owned())?
                .to_owned(),
        );
    }
    let mut metadata = label_metadata(arguments)?;
    metadata
        .as_object_mut()
        .ok_or_else(|| "SESSION_LIFECYCLE_METADATA_INVALID".to_owned())?
        .insert(
            "merge_checkpoints".to_owned(),
            serde_json::to_value(&checkpoint_ids)
                .map_err(|_| "SESSION_LIFECYCLE_CHECKPOINT_IDS_INVALID".to_owned())?,
        );
    let merged = create_session(connection, project_id, &parents, &metadata)?;
    let merged_id = merged["session_id"]
        .as_str()
        .ok_or_else(|| "SESSION_LIFECYCLE_MERGED_ID_INVALID".to_owned())?
        .to_owned();
    append_event(
        connection,
        &merged_id,
        "session-merge",
        &json!({"parents": parents, "checkpoints": checkpoint_ids}),
    )?;
    session(connection, &merged_id, project_id)
}

fn close(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "close")?.to_owned();
    checkpoint_with_metadata(
        connection,
        &session_id,
        project_id,
        &json!({"reason": "close"}),
    )?;
    let updated_at = now_seconds()?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_LIFECYCLE_CLOSE_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "UPDATE sessions SET state='CLOSED',updated_at=?1 \
             WHERE session_id=?2 AND project_id=?3",
            params![updated_at, session_id, project_id],
        )
        .map_err(|error| format!("SESSION_LIFECYCLE_CLOSE_UPDATE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("SESSION_LIFECYCLE_CLOSE_COMMIT_FAILED:{error}"))?;
    session(connection, &session_id, project_id)
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
        [root, action] if root == "session" && action == "fork" => {
            fork(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "merge" => {
            merge(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "session" && action == "close" => {
            close(arguments, &project_id, &mut connection)
        }
        _ => Err("SESSION_LIFECYCLE_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn lifecycle_commands_are_supported() {
        assert!(supports(&["session".to_owned(), "fork".to_owned()]));
        assert!(supports(&["session".to_owned(), "merge".to_owned()]));
        assert!(supports(&["session".to_owned(), "close".to_owned()]));
    }
}
