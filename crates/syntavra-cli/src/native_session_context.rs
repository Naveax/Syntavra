#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

#[derive(Debug, Clone)]
#[allow(clippy::struct_field_names)]
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
    matches!(command, [root, action] if root == "session" && action == "context")
}

fn initialize(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SESSION_CONTEXT_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("SESSION_CONTEXT_DATABASE_OPEN_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_CONTEXT_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "SESSION_CONTEXT_SYSTEM_CLOCK_INVALID".to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_CONTEXT_JSON_SERIALIZE_FAILED".to_owned())
}

fn positional_session_id(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "session" && window[1] == "context")
        .map(|window| window[2].as_str())
        .ok_or_else(|| "SESSION_CONTEXT_ID_MISSING".to_owned())
}

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    let mut value = default;
    let mut index = 0usize;
    while index < arguments.len() {
        let candidate = if arguments[index] == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .as_str(),
            )
        } else {
            arguments[index]
                .strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(candidate) = candidate {
            value = candidate
                .parse::<i64>()
                .map_err(|_| format!("{flag}_VALUE_INVALID"))?;
        }
        index += 1;
    }
    Ok(value)
}

fn require_session(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<(), String> {
    let exists = connection
        .query_row(
            "SELECT 1 FROM sessions WHERE session_id=?1 AND project_id=?2",
            [session_id, project_id],
            |_| Ok(()),
        )
        .optional()
        .map_err(|error| format!("SESSION_CONTEXT_SCOPE_QUERY_FAILED:{error}"))?
        .is_some();
    if exists {
        Ok(())
    } else {
        Err(format!("SESSION_NOT_FOUND:{session_id}"))
    }
}

fn events(
    connection: &Connection,
    session_id: &str,
    project_id: &str,
) -> Result<Vec<Event>, String> {
    require_session(connection, session_id, project_id)?;
    let mut statement = connection
        .prepare(
            "SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at \
             FROM session_events WHERE session_id=?1 ORDER BY sequence",
        )
        .map_err(|error| format!("SESSION_CONTEXT_EVENT_PREPARE_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_CONTEXT_EVENT_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("SESSION_CONTEXT_EVENT_ROW_FAILED:{error}"))?;
        output.push(Event {
            session_id: row.0,
            sequence: row.1,
            event_type: row.2,
            payload: serde_json::from_str(&row.3)
                .map_err(|_| "SESSION_CONTEXT_EVENT_PAYLOAD_INVALID".to_owned())?,
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

#[allow(clippy::too_many_lines)]
fn compact(
    connection: &mut Connection,
    session_id: &str,
    rows: &[Event],
) -> Result<Option<String>, String> {
    if rows.is_empty() {
        return Ok(None);
    }
    let existing = connection
        .query_row(
            "SELECT summary_id,source_end FROM session_summaries \
             WHERE session_id=?1 AND invalidated_at IS NULL ORDER BY source_end DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_CONTEXT_SUMMARY_QUERY_FAILED:{error}"))?;
    if let Some((summary_id, source_end)) = existing {
        if source_end >= rows.last().map_or(0, |event| event.sequence) {
            return Ok(Some(summary_id));
        }
    }

    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_CONTEXT_COMPACT_TRANSACTION_FAILED:{error}"))?;
    let mut level_nodes = Vec::<(String, i64, i64, String)>::new();
    for group in rows.chunks(32) {
        let hashes = group
            .iter()
            .map(|event| Value::String(event.event_hash.clone()))
            .collect::<Vec<_>>();
        let source_hash = sha256_hex(&canonical_bytes(&Value::Array(hashes))?);
        let start = group.first().map_or(0, |event| event.sequence);
        let end = group.last().map_or(0, |event| event.sequence);
        let summary_material = json!({
            "session": session_id,
            "start": start,
            "end": end,
            "hash": source_hash,
            "level": 0,
        });
        let summary_id = format!(
            "sum-{}",
            &sha256_hex(&canonical_bytes(&summary_material)?)[..32]
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
            .map_err(|error| format!("SESSION_CONTEXT_SUMMARY_INSERT_FAILED:{error}"))?;
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
                            format!("SESSION_CONTEXT_SUMMARY_CONTENT_FAILED:{error}")
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
                .map_err(|_| "SESSION_CONTEXT_SUMMARY_CHILDREN_INVALID".to_owned())?;
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
                .map_err(|error| format!("SESSION_CONTEXT_SUMMARY_INSERT_FAILED:{error}"))?;
            next_nodes.push((summary_id, start, end, source_hash));
        }
        level_nodes = next_nodes;
        level += 1;
    }
    let root = level_nodes.first().map(|row| row.0.clone());
    transaction
        .commit()
        .map_err(|error| format!("SESSION_CONTEXT_COMPACT_COMMIT_FAILED:{error}"))?;
    Ok(root)
}

fn json_text(value: &Value) -> Result<String, String> {
    match value {
        Value::Null => Ok("null".to_owned()),
        Value::Bool(value) => Ok(value.to_string()),
        Value::Number(value) => Ok(value.to_string()),
        Value::String(value) => {
            serde_json::to_string(value).map_err(|_| "SESSION_CONTEXT_JSON_TEXT_INVALID".to_owned())
        }
        Value::Array(values) => Ok(format!(
            "[{}]",
            values
                .iter()
                .map(json_text)
                .collect::<Result<Vec<_>, _>>()?
                .join(", ")
        )),
        Value::Object(values) => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort();
            let rows = keys
                .into_iter()
                .map(|key| {
                    Ok(format!(
                        "{}: {}",
                        serde_json::to_string(key)
                            .map_err(|_| "SESSION_CONTEXT_JSON_TEXT_INVALID".to_owned())?,
                        json_text(&values[key])?,
                    ))
                })
                .collect::<Result<Vec<_>, String>>()?;
            Ok(format!("{{{}}}", rows.join(", ")))
        }
    }
}

fn selected_start(length: usize, recent_events: i64) -> usize {
    match recent_events.cmp(&0) {
        std::cmp::Ordering::Greater => {
            length.saturating_sub(usize::try_from(recent_events).unwrap_or(usize::MAX))
        }
        std::cmp::Ordering::Equal => 0,
        std::cmp::Ordering::Less => {
            length.min(usize::try_from(recent_events.unsigned_abs()).unwrap_or(usize::MAX))
        }
    }
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let session_id = positional_session_id(arguments)?;
    let token_budget = integer_option(arguments, "--token-budget", 32_000)?;
    let recent_events = integer_option(arguments, "--recent-events", 24)?;
    let project = project_root.to_string_lossy();
    let project_id = super::super::state_snapshot_contract::project_id_for_root(&project)?;
    let mut connection = initialize(&state_root.join("sessions.sqlite3"))?;
    let rows = events(&connection, session_id, &project_id)?;
    let root = if i128::try_from(rows.len()).unwrap_or(i128::MAX) > i128::from(recent_events) {
        compact(&mut connection, session_id, &rows)?
    } else {
        None
    };
    let start = selected_start(rows.len(), recent_events);
    let selected_events = &rows[start..];
    let mut sections = Vec::<Value>::new();
    if let Some(root_id) = &root {
        let summary = connection
            .query_row(
                "SELECT content,source_start,source_end FROM session_summaries WHERE summary_id=?1",
                [root_id],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                    ))
                },
            )
            .map_err(|error| format!("SESSION_CONTEXT_SUMMARY_ROOT_FAILED:{error}"))?;
        sections.push(json!({
            "role": "summary",
            "id": root_id,
            "text": summary.0,
            "range": [summary.1, summary.2],
        }));
    }
    for event in selected_events {
        sections.push(json!({
            "role": "event",
            "id": format!("event:{}", event.sequence),
            "text": json_text(&json!({
                "type": event.event_type,
                "payload": event.payload,
            }))?,
        }));
    }

    let mut used = 0_i64;
    let mut selected = Vec::new();
    for section in sections.iter().rev() {
        let characters = section["text"]
            .as_str()
            .ok_or_else(|| "SESSION_CONTEXT_SECTION_TEXT_INVALID".to_owned())?
            .chars()
            .count();
        let tokens = i64::try_from(characters / 4 + 1)
            .map_err(|_| "SESSION_CONTEXT_TOKEN_OVERFLOW".to_owned())?
            .max(1);
        if used.saturating_add(tokens) > token_budget {
            continue;
        }
        let mut value = section.clone();
        value
            .as_object_mut()
            .ok_or_else(|| "SESSION_CONTEXT_SECTION_INVALID".to_owned())?
            .insert("estimated_tokens".to_owned(), Value::Number(tokens.into()));
        selected.push(value);
        used = used.saturating_add(tokens);
    }
    selected.reverse();

    Ok(json!({
        "session_id": session_id,
        "budget": token_budget,
        "used": used,
        "sections": selected,
        "root_summary_id": root,
        "recent_event_count": selected_events.len(),
        "exact_history_events": rows.len(),
    }))
}

#[cfg(test)]
mod tests {
    use super::{selected_start, supports};

    #[test]
    fn session_context_command_is_supported() {
        assert!(supports(&["session".to_owned(), "context".to_owned()]));
    }

    #[test]
    fn python_slice_start_rules_are_preserved() {
        assert_eq!(selected_start(10, 3), 7);
        assert_eq!(selected_start(10, 0), 0);
        assert_eq!(selected_start(10, -3), 3);
        assert_eq!(selected_start(2, -50), 2);
    }
}
