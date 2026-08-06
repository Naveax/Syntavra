#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension as _};
use serde_json::{json, Value};

use super::native_backup::{initialize_evidence_state, set_private};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "compress" && action == "describe")
}

fn parse_arguments(arguments: &[String]) -> Result<String, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "compress" && row[1] == "describe")
        .map(|index| index + 2)
        .ok_or_else(|| "COMPRESSION_DESCRIBE_COMMAND_MISSING".to_owned())?;
    let compression_id = arguments[start..]
        .iter()
        .find(|value| !value.starts_with('-'))
        .ok_or_else(|| "COMPRESSION_ID_MISSING".to_owned())?;
    if compression_id.is_empty() {
        return Err("COMPRESSION_ID_INVALID".to_owned());
    }
    Ok(compression_id.clone())
}

fn database_path(state_root: &Path) -> PathBuf {
    state_root.join("compression.sqlite3")
}

fn initialize_database(state_root: &Path) -> Result<PathBuf, String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("COMPRESSION_STATE_ROOT_CREATE_FAILED:{error}"))?;
    let path = database_path(state_root);
    let connection = Connection::open(&path)
        .map_err(|error| format!("COMPRESSION_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .query_row("PRAGMA journal_mode=WAL", [], |row| row.get::<_, String>(0))
        .map_err(|error| format!("COMPRESSION_DATABASE_WAL_FAILED:{error}"))?;
    connection
        .execute_batch(
            r#"
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=30000;
            PRAGMA synchronous=FULL;
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS compressions(
                compression_id TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                exact_handle TEXT NOT NULL,
                original_bytes INTEGER NOT NULL,
                visible_text TEXT NOT NULL,
                chunk_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                receipt_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS compression_chunks(
                compression_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_handle TEXT NOT NULL,
                chunk_bytes INTEGER NOT NULL,
                PRIMARY KEY(compression_id,chunk_index),
                FOREIGN KEY(compression_id) REFERENCES compressions(compression_id) ON DELETE CASCADE
            );
            COMMIT;
            "#,
        )
        .map_err(|error| format!("COMPRESSION_DATABASE_SCHEMA_FAILED:{error}"))?;
    drop(connection);
    set_private(&path);
    Ok(path)
}

fn describe(path: &Path, compression_id: &str) -> Result<Value, String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("COMPRESSION_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch("PRAGMA foreign_keys=ON; PRAGMA busy_timeout=30000;")
        .map_err(|error| format!("COMPRESSION_DATABASE_PRAGMA_FAILED:{error}"))?;

    let record = connection
        .query_row(
            r#"
            SELECT compression_id, content_type, exact_handle, original_bytes,
                   visible_text, chunk_size, chunk_count, metadata_json,
                   receipt_hash, created_at
            FROM compressions
            WHERE compression_id=?1
            "#,
            params![compression_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, f64>(9)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("COMPRESSION_DESCRIBE_QUERY_FAILED:{error}"))?
        .ok_or_else(|| format!("COMPRESSION_NOT_FOUND:{compression_id}"))?;

    let metadata = serde_json::from_str::<Value>(&record.7)
        .map_err(|error| format!("COMPRESSION_METADATA_INVALID:{error}"))?;
    let mut statement = connection
        .prepare(
            r#"
            SELECT compression_id, chunk_index, chunk_handle, chunk_bytes
            FROM compression_chunks
            WHERE compression_id=?1
            ORDER BY chunk_index
            "#,
        )
        .map_err(|error| format!("COMPRESSION_CHUNK_QUERY_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(params![compression_id], |row| {
            Ok(json!({
                "compression_id": row.get::<_, String>(0)?,
                "chunk_index": row.get::<_, i64>(1)?,
                "chunk_handle": row.get::<_, String>(2)?,
                "chunk_bytes": row.get::<_, i64>(3)?,
            }))
        })
        .map_err(|error| format!("COMPRESSION_CHUNK_QUERY_FAILED:{error}"))?;
    let mut chunks = Vec::new();
    for row in rows {
        chunks.push(row.map_err(|error| format!("COMPRESSION_CHUNK_ROW_FAILED:{error}"))?);
    }

    Ok(json!({
        "compression_id": record.0,
        "content_type": record.1,
        "exact_handle": record.2,
        "original_bytes": record.3,
        "visible_text": record.4,
        "chunk_size": record.5,
        "chunk_count": record.6,
        "receipt_hash": record.8,
        "created_at": record.9,
        "metadata": metadata,
        "chunks": chunks,
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    initialize_evidence_state(state_root)?;
    let compression_id = parse_arguments(arguments)?;
    let path = initialize_database(state_root)?;
    describe(&path, &compression_id)
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, supports};

    #[test]
    fn routes_compress_describe_only() {
        assert!(supports(&["compress".to_owned(), "describe".to_owned()]));
        assert!(!supports(&["compress".to_owned(), "put".to_owned()]));
    }

    #[test]
    fn parses_compression_identifier() {
        let value = parse_arguments(&[
            "compress".to_owned(),
            "describe".to_owned(),
            "ccr-example".to_owned(),
        ])
        .expect("parse");
        assert_eq!(value, "ccr-example");
    }
}
