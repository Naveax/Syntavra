#![forbid(unsafe_code)]

use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "stats")
        || matches!(command, [root, action] if root == "run" && action == "evidence-stats")
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

fn runtime_evidence_stats(state_root: &Path) -> Result<Value, String> {
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

pub fn execute(command: &[String], state_root: &Path) -> Result<Value, String> {
    match command {
        [root, action] if root == "evidence" && action == "stats" => {
            evidence_store_stats(state_root)
        }
        [root, action] if root == "run" && action == "evidence-stats" => {
            runtime_evidence_stats(state_root)
        }
        _ => Err("EVIDENCE_STATS_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn evidence_statistics_commands_are_supported() {
        assert!(supports(&["evidence".to_owned(), "stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-stats".to_owned()]));
    }
}
