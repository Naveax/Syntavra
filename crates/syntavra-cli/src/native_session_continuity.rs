#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

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
    matches!(command, [root, action]
        if root == "run" && matches!(action.as_str(), "session-compact" | "session-continuity"))
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

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "SESSION_JSON_SERIALIZE_FAILED".to_owned())
}

fn positional_after<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == action)
        .ok_or_else(|| "SESSION_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| format!("SESSION_ID_MISSING:{action}"))
}

fn option_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    let mut value = None;
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
            if value.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            value = Some(
                current
                    .parse::<i64>()
                    .map_err(|_| format!("{flag}_INVALID"))?,
            );
        }
        index += 1;
    }
    Ok(value.unwrap_or(default))
}

fn flag_present(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn session(connection: &Connection, session_id: &str, project_id: &str) -> Result<Value, String> {
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
        .map_err(|error| format!("SESSION_QUERY_FAILED:{error}"))?
        .ok_or_else(|| format!("SESSION_NOT_FOUND:{session_id}"))?;
    let parents: Value =
        serde_json::from_str(&row.2).map_err(|_| "SESSION_PARENT_IDS_INVALID".to_owned())?;
    let metadata: Value =
        serde_json::from_str(&row.6).map_err(|_| "SESSION_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "session_id": row.0,
        "project_id": row.1,
        "parent_ids": parents,
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
        .map_err(|error| format!("SESSION_EVENT_PREPARE_FAILED:{error}"))?;
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
        .map_err(|error| format!("SESSION_EVENT_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("SESSION_EVENT_ROW_FAILED:{error}"))?;
        output.push(Event {
            session_id: row.0,
            sequence: row.1,
            event_type: row.2,
            payload: serde_json::from_str(&row.3)
                .map_err(|_| "SESSION_EVENT_PAYLOAD_INVALID".to_owned())?,
            previous_hash: row.4,
            event_hash: row.5,
            created_at: row.6,
        });
    }
    Ok(output)
}

fn verify(connection: &Connection, session_id: &str, project_id: &str) -> Result<Value, String> {
    let rows = events(connection, session_id, project_id)?;
    let mut reasons = Vec::new();
    let mut previous = ZERO_HASH.to_owned();
    let mut expected_sequence = 1_i64;
    for event in &rows {
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
        Value::Object(values) => {
            let mut rows = Vec::new();
            for (key, value) in values {
                rows.push(format!(
                    "'{}': {}",
                    key.replace('\'', "\\'"),
                    python_repr(value)
                ));
            }
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
    project_id: &str,
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
            .map_err(|error| format!("SESSION_SUMMARY_QUERY_FAILED:{error}"))?;
        if let Some((summary_id, source_end)) = existing {
            if source_end >= rows.last().map_or(0, |event| event.sequence) {
                return Ok(Some(summary_id));
            }
        }
    }

    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("SESSION_COMPACT_TRANSACTION_FAILED:{error}"))?;
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
            .map_err(|error| format!("SESSION_SUMMARY_INSERT_FAILED:{error}"))?;
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
                        .map_err(|error| format!("SESSION_SUMMARY_CONTENT_FAILED:{error}"))?,
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
                .map_err(|_| "SESSION_SUMMARY_CHILDREN_INVALID".to_owned())?;
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
                .map_err(|error| format!("SESSION_SUMMARY_INSERT_FAILED:{error}"))?;
            next_nodes.push((summary_id, start, end, source_hash));
        }
        level_nodes = next_nodes;
        level += 1;
    }
    let root = level_nodes.first().map(|row| row.0.clone());
    transaction
        .commit()
        .map_err(|error| format!("SESSION_COMPACT_COMMIT_FAILED:{error}"))?;
    Ok(root)
}

fn python_json(value: &Value) -> Result<String, String> {
    match value {
        Value::Null => Ok("null".to_owned()),
        Value::Bool(value) => Ok(value.to_string()),
        Value::Number(value) => Ok(value.to_string()),
        Value::String(value) => {
            serde_json::to_string(value).map_err(|_| "SESSION_CONTEXT_JSON_INVALID".to_owned())
        }
        Value::Array(values) => Ok(format!(
            "[{}]",
            values
                .iter()
                .map(python_json)
                .collect::<Result<Vec<_>, _>>()?
                .join(", ")
        )),
        Value::Object(values) => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort();
            let mut rows = Vec::new();
            for key in keys {
                rows.push(format!(
                    "{}: {}",
                    serde_json::to_string(key)
                        .map_err(|_| "SESSION_CONTEXT_JSON_INVALID".to_owned())?,
                    python_json(&values[key])?,
                ));
            }
            Ok(format!("{{{}}}", rows.join(", ")))
        }
    }
}

fn active_context(
    connection: &mut Connection,
    session_id: &str,
    project_id: &str,
    token_budget: i64,
) -> Result<Value, String> {
    let rows = events(connection, session_id, project_id)?;
    let root = if rows.len() > 24 {
        compact(connection, session_id, project_id, false)?
    } else {
        None
    };
    let selected_events = rows.iter().rev().take(24).cloned().collect::<Vec<_>>();
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
            .map_err(|error| format!("SESSION_SUMMARY_ROOT_FAILED:{error}"))?;
        sections.push(json!({
            "role": "summary",
            "id": root_id,
            "text": summary.0,
            "range": [summary.1, summary.2],
        }));
    }
    for event in selected_events.iter().rev() {
        let text = python_json(&json!({
            "type": event.event_type,
            "payload": event.payload,
        }))?;
        sections.push(json!({
            "role": "event",
            "id": format!("event:{}", event.sequence),
            "text": text,
        }));
    }
    let mut used = 0_i64;
    let mut selected = Vec::new();
    for section in sections.iter().rev() {
        let characters = section["text"]
            .as_str()
            .ok_or_else(|| "SESSION_CONTEXT_TEXT_INVALID".to_owned())?
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
        used += tokens;
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

fn compact_once(
    arguments: &[String],
    project_id: &str,
    state_root: &Path,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "session-compact")?;
    let force = flag_present(arguments, "--force");
    let started = Instant::now();
    let root = compact(connection, session_id, project_id, force)?;
    let verification = verify(connection, session_id, project_id)?;
    let context = active_context(connection, session_id, project_id, 32_000)?;
    let wall_time_ms = started.elapsed().as_secs_f64() * 1000.0;
    let ok = verification["ok"].as_bool().unwrap_or(false)
        && (root.is_some() || verification["events"].as_i64() == Some(0));
    let value = json!({
        "ok": ok,
        "session_id": session_id,
        "root_summary_id": root,
        "events": verification["events"],
        "active_context_tokens": context["used"],
        "exact_history_events": context["exact_history_events"],
        "wall_time_ms": wall_time_ms,
        "verification": verification,
    });
    super::native_analytics::record_event(
        &json!({
            "session_id": session_id,
            "repository_hash": project_id,
            "kind": "session-compaction",
            "compaction_ms": wall_time_ms,
            "wall_time_ms": wall_time_ms,
            "success": ok,
            "metadata": {
                "events": value["events"],
                "root_summary_id": value["root_summary_id"],
                "active_context_tokens": value["active_context_tokens"],
            },
        }),
        state_root,
    )?;
    Ok(value)
}

fn continuity(
    arguments: &[String],
    project_id: &str,
    state_root: &Path,
    connection: &mut Connection,
) -> Result<Value, String> {
    let session_id = positional_after(arguments, "session-continuity")?;
    let token_budget = option_i64(arguments, "--token-budget", 32_000)?;
    let started = Instant::now();
    let session = session(connection, session_id, project_id)?;
    let verification = verify(connection, session_id, project_id)?;
    let context = active_context(connection, session_id, project_id, token_budget)?;
    let exact_recovery = if let Some(root) = context["root_summary_id"].as_str() {
        let coverage = connection
            .query_row(
                "SELECT source_start,source_end,invalidated_at FROM session_summaries WHERE summary_id=?1",
                [root],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, Option<f64>>(2)?,
                    ))
                },
            )
            .optional()
            .map_err(|error| format!("SESSION_SUMMARY_EXPAND_FAILED:{error}"))?;
        match coverage {
            Some((source_start, source_end, None)) => {
                let event_count = connection
                    .query_row(
                        "SELECT COUNT(*) FROM session_events                          WHERE session_id=?1 AND sequence BETWEEN ?2 AND ?3",
                        params![session_id, source_start, source_end],
                        |row| row.get::<_, i64>(0),
                    )
                    .map_err(|error| format!("SESSION_SUMMARY_EVENT_COUNT_FAILED:{error}"))?;
                event_count == context["exact_history_events"].as_i64().unwrap_or(-1)
            }
            Some((_, _, Some(_))) | None => false,
        }
    } else {
        true
    };
    let restored = verification["ok"].as_bool().unwrap_or(false);
    let wall_time_ms = started.elapsed().as_secs_f64() * 1000.0;
    let claim = if restored && exact_recovery {
        "SESSION_CONTINUITY_INTERNALLY_VERIFIED"
    } else {
        "SESSION_CONTINUITY_NOT_PROVEN"
    };
    let receipt = json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "session_id": session_id,
        "project_id": session["project_id"],
        "state": session["state"],
        "parents": session["parent_ids"],
        "events": verification["events"],
        "last_event_hash": verification["last_hash"],
        "active_context_tokens": context["used"],
        "token_budget": token_budget,
        "root_summary_id": context["root_summary_id"],
        "exact_recovery": exact_recovery,
        "forced_restart": false,
        "continuity_restored": restored,
        "wall_time_ms": wall_time_ms,
        "claim": claim,
    });
    super::native_analytics::record_event(
        &json!({
            "session_id": session_id,
            "repository_hash": project_id,
            "kind": "session-continuity-receipt",
            "wall_time_ms": wall_time_ms,
            "success": restored && exact_recovery,
            "continuity_restored": restored,
            "metadata": {
                "events": receipt["events"],
                "exact_recovery": exact_recovery,
                "active_context_tokens": receipt["active_context_tokens"],
            },
        }),
        state_root,
    )?;
    Ok(receipt)
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
        Some("session-compact") => {
            compact_once(arguments, &project_id, state_root, &mut connection)
        }
        Some("session-continuity") => {
            continuity(arguments, &project_id, state_root, &mut connection)
        }
        _ => Err("SESSION_CONTINUITY_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn session_continuity_commands_are_supported() {
        for action in ["session-compact", "session-continuity"] {
            let command = ["run", action]
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            assert!(supports(&command));
        }
    }
}
