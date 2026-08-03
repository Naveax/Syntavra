#![forbid(unsafe_code)]

use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Value};

type EdgeRow = (String, String, String, String, f64, String, String, String);

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "stats")
        || matches!(command, [root, action]
            if root == "run" && matches!(action.as_str(), "evidence-stats" | "evidence-neighbors"))
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "EVIDENCE_SYSTEM_CLOCK_INVALID".to_owned())
}

fn active_key_version(keys_root: &Path) -> Result<i64, String> {
    fs::create_dir_all(keys_root)
        .map_err(|error| format!("EVIDENCE_KEY_DIRECTORY_CREATE_FAILED:{error}"))?;
    let marker = keys_root.join("active.json");
    let value = match fs::read_to_string(&marker) {
        Ok(text) => serde_json::from_str::<Value>(&text)
            .map_err(|_| "EVIDENCE_ACTIVE_KEY_MARKER_INVALID".to_owned())?,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            let value = json!({"schema_version": 1, "active_version": 1});
            let encoded = serde_json::to_vec_pretty(&value)
                .map_err(|_| "EVIDENCE_ACTIVE_KEY_MARKER_INVALID".to_owned())?;
            fs::write(&marker, encoded)
                .map_err(|write_error| format!("EVIDENCE_ACTIVE_KEY_MARKER_WRITE_FAILED:{write_error}"))?;
            value
        }
        Err(error) => return Err(format!("EVIDENCE_ACTIVE_KEY_MARKER_READ_FAILED:{error}")),
    };
    let version = value
        .get("active_version")
        .and_then(Value::as_i64)
        .ok_or_else(|| "EVIDENCE_ACTIVE_KEY_MARKER_INVALID".to_owned())?;
    if version < 1 {
        return Err("EVIDENCE_ACTIVE_KEY_VERSION_INVALID".to_owned());
    }
    Ok(version)
}

fn evidence_store_stats(state_root: &Path) -> Result<Value, String> {
    let root = state_root.join("evidence");
    fs::create_dir_all(root.join("objects"))
        .and_then(|_| fs::create_dir_all(root.join("metadata")))
        .map_err(|error| format!("EVIDENCE_DIRECTORY_CREATE_FAILED:{error}"))?;
    let active_version = active_key_version(&root.join("keys"))?;
    let connection = Connection::open(root.join("evidence.sqlite3"))
        .map_err(|error| format!("EVIDENCE_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             CREATE TABLE IF NOT EXISTS evidence_objects(\
               digest TEXT PRIMARY KEY,plaintext_bytes INTEGER NOT NULL,stored_bytes INTEGER NOT NULL,\
               key_version INTEGER NOT NULL,created_at REAL NOT NULL,last_accessed_at REAL NOT NULL,\
               expires_at REAL,ref_count INTEGER NOT NULL DEFAULT 0,legal_hold INTEGER NOT NULL DEFAULT 0);\
             CREATE TABLE IF NOT EXISTS evidence_references(\
               digest TEXT NOT NULL,reference TEXT NOT NULL,created_at REAL NOT NULL,\
               PRIMARY KEY(digest,reference),\
               FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS evidence_expiry_idx ON evidence_objects(expires_at);",
        )
        .map_err(|error| format!("EVIDENCE_DATABASE_INITIALIZE_FAILED:{error}"))?;
    let row = connection
        .query_row(
            "SELECT COUNT(*),COALESCE(SUM(plaintext_bytes),0),COALESCE(SUM(stored_bytes),0),\
             COALESCE(SUM(ref_count),0) FROM evidence_objects",
            [],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, i64>(3)?,
                ))
            },
        )
        .map_err(|error| format!("EVIDENCE_STATS_QUERY_FAILED:{error}"))?;
    let collectable = connection
        .query_row(
            "SELECT COUNT(*) FROM evidence_objects \
             WHERE expires_at IS NOT NULL AND expires_at<=?1 AND ref_count=0 AND legal_hold=0",
            [now_seconds()?],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|error| format!("EVIDENCE_COLLECTABLE_QUERY_FAILED:{error}"))?;
    Ok(json!({
        "objects": row.0,
        "plaintext_bytes": row.1,
        "stored_bytes": row.2,
        "references": row.3,
        "collectable": collectable,
        "encrypted": true,
        "active_key_version": active_version,
    }))
}

fn runtime_evidence_connection(state_root: &Path) -> Result<Connection, String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("RUNTIME_EVIDENCE_DIRECTORY_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(state_root.join("runtime-evidence.sqlite3"))
        .map_err(|error| format!("RUNTIME_EVIDENCE_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS nodes(\
               node_id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,source TEXT NOT NULL,\
               confidence REAL NOT NULL,repository_commit TEXT NOT NULL,metadata_json TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS edges(\
               evidence TEXT PRIMARY KEY,source TEXT NOT NULL,target TEXT NOT NULL,relation TEXT NOT NULL,\
               confidence REAL NOT NULL,repository_commit TEXT NOT NULL,observed_at TEXT NOT NULL,\
               metadata_json TEXT NOT NULL);\
             CREATE INDEX IF NOT EXISTS idx_evidence_source ON edges(source,relation);\
             CREATE INDEX IF NOT EXISTS idx_evidence_target ON edges(target,relation);",
        )
        .map_err(|error| format!("RUNTIME_EVIDENCE_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn runtime_evidence_stats(state_root: &Path) -> Result<Value, String> {
    let connection = runtime_evidence_connection(state_root)?;
    let nodes = connection
        .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get::<_, i64>(0))
        .map_err(|error| format!("RUNTIME_EVIDENCE_NODE_COUNT_FAILED:{error}"))?;
    let edges = connection
        .query_row("SELECT COUNT(*) FROM edges", [], |row| row.get::<_, i64>(0))
        .map_err(|error| format!("RUNTIME_EVIDENCE_EDGE_COUNT_FAILED:{error}"))?;
    let mut statement = connection
        .prepare("SELECT relation,COUNT(*) FROM edges GROUP BY relation ORDER BY relation")
        .map_err(|error| format!("RUNTIME_EVIDENCE_RELATION_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)))
        .map_err(|error| format!("RUNTIME_EVIDENCE_RELATION_QUERY_FAILED:{error}"))?;
    let mut relations = Vec::new();
    for row in rows {
        let (relation, count) =
            row.map_err(|error| format!("RUNTIME_EVIDENCE_RELATION_ROW_FAILED:{error}"))?;
        relations.push(json!({"relation": relation, "count": count}));
    }
    Ok(json!({"ok": true, "nodes": nodes, "edges": edges, "relations": relations}))
}

fn edge_row(row: &Row<'_>) -> rusqlite::Result<EdgeRow> {
    Ok((
        row.get(0)?,
        row.get(1)?,
        row.get(2)?,
        row.get(3)?,
        row.get(4)?,
        row.get(5)?,
        row.get(6)?,
        row.get(7)?,
    ))
}

fn linked_node(connection: &Connection, node_id: &str) -> Result<Value, String> {
    let row = connection
        .query_row(
            "SELECT node_id,kind,label,source,confidence,repository_commit,metadata_json \
             FROM nodes WHERE node_id=?1",
            [node_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, f64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("RUNTIME_EVIDENCE_LINKED_NODE_QUERY_FAILED:{error}"))?;
    Ok(match row {
        Some(row) => json!({
            "node_id": row.0,
            "kind": row.1,
            "label": row.2,
            "source": row.3,
            "confidence": row.4,
            "repository_commit": row.5,
            "metadata_json": row.6,
        }),
        None => Value::Null,
    })
}

fn neighbor_value(
    connection: &Connection,
    row: EdgeRow,
    linked_node_id: &str,
) -> Result<Value, String> {
    let metadata: Value = serde_json::from_str(&row.7)
        .map_err(|_| "RUNTIME_EVIDENCE_EDGE_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "evidence": row.0,
        "source": row.1,
        "target": row.2,
        "relation": row.3,
        "confidence": row.4,
        "repository_commit": row.5,
        "observed_at": row.6,
        "metadata": metadata,
        "node": linked_node(connection, linked_node_id)?,
    }))
}

fn argument_after<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == action)
        .ok_or_else(|| "RUNTIME_EVIDENCE_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| "RUNTIME_EVIDENCE_NODE_ID_MISSING".to_owned())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            value = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(current) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            value = Some(current.to_owned());
        }
        index += 1;
    }
    Ok(value)
}

fn runtime_evidence_neighbors(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let node_id = argument_after(arguments, "evidence-neighbors")?;
    let relation = option_value(arguments, "--relation")?;
    let reverse = arguments.iter().any(|value| value == "--reverse");
    let connection = runtime_evidence_connection(state_root)?;
    let direction = if reverse { "target" } else { "source" };
    let query = format!(
        "SELECT evidence,source,target,relation,confidence,repository_commit,observed_at,metadata_json \
         FROM edges WHERE {direction}=?1{} ORDER BY observed_at DESC",
        if relation.is_some() { " AND relation=?2" } else { "" }
    );
    let mut statement = connection
        .prepare(&query)
        .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_PREPARE_FAILED:{error}"))?;
    let mut neighbors = Vec::new();
    if let Some(relation) = relation {
        let rows = statement
            .query_map(params![node_id, relation], edge_row)
            .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_QUERY_FAILED:{error}"))?;
        for row in rows {
            let row = row.map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_ROW_FAILED:{error}"))?;
            let linked = if reverse { row.1.clone() } else { row.2.clone() };
            neighbors.push(neighbor_value(&connection, row, &linked)?);
        }
    } else {
        let rows = statement
            .query_map([node_id], edge_row)
            .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_QUERY_FAILED:{error}"))?;
        for row in rows {
            let row = row.map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_ROW_FAILED:{error}"))?;
            let linked = if reverse { row.1.clone() } else { row.2.clone() };
            neighbors.push(neighbor_value(&connection, row, &linked)?);
        }
    }
    Ok(json!({"ok": true, "neighbors": neighbors}))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    match command {
        [root, action] if root == "evidence" && action == "stats" => {
            evidence_store_stats(state_root)
        }
        [root, action] if root == "run" && action == "evidence-stats" => {
            runtime_evidence_stats(state_root)
        }
        [root, action] if root == "run" && action == "evidence-neighbors" => {
            runtime_evidence_neighbors(arguments, state_root)
        }
        _ => Err("EVIDENCE_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn evidence_commands_are_supported() {
        assert!(supports(&["evidence".to_owned(), "stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-neighbors".to_owned()]));
    }
}
