#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use rusqlite::Connection;

fn open(path: &Path, schema: &str, label: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("PLATFORM_STATE_{label}_PARENT_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("PLATFORM_STATE_{label}_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch("PRAGMA busy_timeout=30000; PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL;")
        .map_err(|error| format!("PLATFORM_STATE_{label}_PRAGMA_FAILED:{error}"))?;
    connection
        .execute_batch(schema)
        .map_err(|error| format!("PLATFORM_STATE_{label}_SCHEMA_FAILED:{error}"))?;
    Ok(())
}

fn artifacts(root: &Path) -> Result<(), String> {
    open(
        &root.join("artifacts").join("artifacts.sqlite3"),
        "CREATE TABLE IF NOT EXISTS artifacts(\
            artifact_id TEXT PRIMARY KEY,\
            kind TEXT NOT NULL,\
            media_type TEXT NOT NULL,\
            content_hash TEXT NOT NULL,\
            payload BLOB NOT NULL,\
            metadata_json TEXT NOT NULL DEFAULT '{}',\
            created_at REAL NOT NULL\
        );",
        "ARTIFACTS",
    )
}

fn headless(root: &Path) -> Result<(), String> {
    open(
        &root.join("headless.sqlite3"),
        "CREATE TABLE IF NOT EXISTS jobs(\
            job_id TEXT PRIMARY KEY,\
            state TEXT NOT NULL,\
            command_json TEXT NOT NULL,\
            workspace TEXT NOT NULL,\
            policy_json TEXT NOT NULL,\
            metadata_json TEXT NOT NULL,\
            created_at REAL NOT NULL,\
            updated_at REAL NOT NULL\
        );\
        CREATE TABLE IF NOT EXISTS events(\
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,\
            job_id TEXT NOT NULL,\
            event_type TEXT NOT NULL,\
            payload_json TEXT NOT NULL,\
            created_at REAL NOT NULL\
        );",
        "HEADLESS",
    )
}

fn evidence(root: &Path) -> Result<(), String> {
    open(
        &root.join("runtime-evidence.sqlite3"),
        "CREATE TABLE IF NOT EXISTS nodes(\
            node_id TEXT PRIMARY KEY,\
            kind TEXT NOT NULL,\
            payload_json TEXT NOT NULL,\
            created_at REAL NOT NULL\
        );\
        CREATE TABLE IF NOT EXISTS edges(\
            source_id TEXT NOT NULL,\
            target_id TEXT NOT NULL,\
            relation TEXT NOT NULL,\
            metadata_json TEXT NOT NULL DEFAULT '{}',\
            created_at REAL NOT NULL,\
            PRIMARY KEY(source_id,target_id,relation)\
        );",
        "EVIDENCE",
    )
}

fn capability(root: &Path) -> Result<(), String> {
    let security = root.join("security");
    fs::create_dir_all(&security)
        .map_err(|error| format!("PLATFORM_STATE_SECURITY_CREATE_FAILED:{error}"))?;
    let key = security.join("capability.key");
    if !key.is_file() {
        fs::write(&key, b"syntavra-native-capability-key-v1\n")
            .map_err(|error| format!("PLATFORM_STATE_CAPABILITY_KEY_FAILED:{error}"))?;
    }
    open(
        &security.join("capability.sqlite3"),
        "CREATE TABLE IF NOT EXISTS consumed(\
            token_hash TEXT PRIMARY KEY,\
            consumed_at REAL NOT NULL,\
            metadata_json TEXT NOT NULL DEFAULT '{}'\
        );",
        "CAPABILITY",
    )
}

fn semantic(root: &Path) -> Result<(), String> {
    open(
        &root.join("semantic-graph.sqlite3"),
        "CREATE TABLE IF NOT EXISTS files(\
            path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'\
        );\
        CREATE TABLE IF NOT EXISTS nodes(\
            node_id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,\
            start_line INTEGER NOT NULL DEFAULT 0, end_line INTEGER NOT NULL DEFAULT 0,\
            metadata_json TEXT NOT NULL DEFAULT '{}'\
        );\
        CREATE TABLE IF NOT EXISTS edges(\
            source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,\
            metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(source_id,target_id,relation)\
        );\
        CREATE TABLE IF NOT EXISTS semantic_sources(\
            source_key TEXT PRIMARY KEY, format TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'\
        );\
        CREATE TABLE IF NOT EXISTS semantic_source_nodes(\
            source_key TEXT NOT NULL, node_id TEXT NOT NULL, PRIMARY KEY(source_key,node_id)\
        );\
        CREATE TABLE IF NOT EXISTS semantic_source_edges(\
            source_key TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,\
            PRIMARY KEY(source_key,source_id,target_id,relation)\
        );\
        CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(node_id UNINDEXED,name,kind,path);",
        "SEMANTIC",
    )
}

fn sessions(root: &Path) -> Result<(), String> {
    open(
        &root.join("session-memory.sqlite3"),
        "CREATE TABLE IF NOT EXISTS sessions(\
            session_id TEXT PRIMARY KEY, parent_ids_json TEXT NOT NULL, state TEXT NOT NULL,\
            metadata_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL\
        );\
        CREATE TABLE IF NOT EXISTS events(\
            session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,\
            payload_json TEXT NOT NULL, event_hash TEXT NOT NULL, created_at REAL NOT NULL,\
            PRIMARY KEY(session_id,sequence)\
        );\
        CREATE TABLE IF NOT EXISTS summaries(\
            summary_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, content TEXT NOT NULL,\
            metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL\
        );\
        CREATE TABLE IF NOT EXISTS checkpoints(\
            checkpoint_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',\
            metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL\
        );",
        "SESSION_MEMORY",
    )
}

pub fn initialize(state_root: &Path) -> Result<(), String> {
    let root = state_root.join("unified");
    fs::create_dir_all(&root)
        .map_err(|error| format!("PLATFORM_STATE_ROOT_CREATE_FAILED:{error}"))?;
    artifacts(&root)?;
    headless(&root)?;
    evidence(&root)?;
    capability(&root)?;
    semantic(&root)?;
    sessions(&root)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::initialize;
    use std::fs;

    #[test]
    fn creates_shared_state_tree() {
        let root =
            std::env::temp_dir().join(format!("syntavra-platform-state-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        initialize(&root).unwrap();
        assert!(root.join("unified/headless.sqlite3").is_file());
        assert!(root.join("unified/security/capability.key").is_file());
        let _ = fs::remove_dir_all(root);
    }
}
