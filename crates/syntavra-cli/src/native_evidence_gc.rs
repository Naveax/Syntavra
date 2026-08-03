#![forbid(unsafe_code)]

use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};

const DEFAULT_TTL_DAYS: f64 = 30.0;
const DEFAULT_MAX_DELETE_BYTES: i64 = 1024 * 1024 * 1024;
const LIMIT: i64 = 1000;

#[derive(Debug)]
struct Candidate {
    digest: String,
    plaintext_bytes: i64,
    stored_bytes: i64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "gc")
        || matches!(command, [root, action] if root == "maintenance" && action == "janitor")
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "EVIDENCE_GC_SYSTEM_CLOCK_INVALID".to_owned())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            let current = arguments
                .get(index)
                .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?;
            if value.replace(current.clone()).is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
        } else if let Some(current) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            if value.replace(current.to_owned()).is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
        }
        index += 1;
    }
    Ok(value)
}

fn option_f64(arguments: &[String], flag: &str, default: f64) -> Result<f64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<f64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn option_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn remove_if_present(path: &Path) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("EVIDENCE_GC_FILE_REMOVE_FAILED:{error}")),
    }
}

fn object_path(root: &Path, digest: &str) -> Result<PathBuf, String> {
    let prefix = digest
        .get(..2)
        .ok_or_else(|| "EVIDENCE_GC_DIGEST_INVALID".to_owned())?;
    let suffix = digest
        .get(2..)
        .ok_or_else(|| "EVIDENCE_GC_DIGEST_INVALID".to_owned())?;
    Ok(root.join("objects").join(prefix).join(suffix))
}

fn initialize(connection: &Connection) -> Result<(), String> {
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
        .map_err(|error| format!("EVIDENCE_GC_DATABASE_INITIALIZE_FAILED:{error}"))
}

fn collect(
    state_root: &Path,
    ttl_days: f64,
    max_delete_bytes: Option<i64>,
    dry_run: bool,
) -> Result<Value, String> {
    let root = state_root.join("evidence");
    fs::create_dir_all(root.join("objects"))
        .and_then(|_| fs::create_dir_all(root.join("metadata")))
        .map_err(|error| format!("EVIDENCE_GC_DIRECTORY_CREATE_FAILED:{error}"))?;
    let mut connection = Connection::open(root.join("evidence.sqlite3"))
        .map_err(|error| format!("EVIDENCE_GC_DATABASE_OPEN_FAILED:{error}"))?;
    initialize(&connection)?;
    let cutoff_now = now_seconds()?;
    let ttl_seconds = ttl_days.max(0.0) * 86_400.0;
    let age_cutoff = cutoff_now - ttl_seconds;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("EVIDENCE_GC_TRANSACTION_FAILED:{error}"))?;
    let rows = {
        let mut statement = transaction
            .prepare(
                "SELECT digest,plaintext_bytes,stored_bytes FROM evidence_objects \
                 WHERE ref_count=0 AND legal_hold=0 AND (\
                   (expires_at IS NOT NULL AND expires_at<=?1) OR last_accessed_at<=?2\
                 ) ORDER BY COALESCE(expires_at,last_accessed_at),last_accessed_at LIMIT ?3",
            )
            .map_err(|error| format!("EVIDENCE_GC_QUERY_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(params![cutoff_now, age_cutoff, LIMIT], |row| {
                Ok(Candidate {
                    digest: row.get(0)?,
                    plaintext_bytes: row.get(1)?,
                    stored_bytes: row.get(2)?,
                })
            })
            .map_err(|error| format!("EVIDENCE_GC_QUERY_FAILED:{error}"))?;
        let mut values = Vec::new();
        for row in mapped {
            values.push(row.map_err(|error| format!("EVIDENCE_GC_ROW_FAILED:{error}"))?);
        }
        values
    };

    let mut selected = Vec::new();
    let mut consumed = 0_i64;
    for row in rows {
        if let Some(maximum) = max_delete_bytes {
            if !selected.is_empty() && consumed.saturating_add(row.stored_bytes) > maximum {
                break;
            }
            if selected.is_empty() && row.stored_bytes > maximum {
                continue;
            }
        }
        consumed = consumed.saturating_add(row.stored_bytes);
        selected.push(row);
    }

    if !dry_run {
        for row in &selected {
            remove_if_present(&object_path(&root, &row.digest)?)?;
            remove_if_present(&root.join("metadata").join(format!("{}.json", row.digest)))?;
            transaction
                .execute(
                    "DELETE FROM evidence_objects WHERE digest=?1",
                    [&row.digest],
                )
                .map_err(|error| format!("EVIDENCE_GC_DELETE_FAILED:{error}"))?;
        }
    }
    let plaintext_bytes = selected.iter().fold(0_i64, |total, row| {
        total.saturating_add(row.plaintext_bytes)
    });
    let objects = i64::try_from(selected.len())
        .map_err(|_| "EVIDENCE_GC_OBJECT_COUNT_OVERFLOW".to_owned())?;
    transaction
        .commit()
        .map_err(|error| format!("EVIDENCE_GC_COMMIT_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "dry_run": dry_run,
        "objects": objects,
        "deleted": if dry_run { 0 } else { objects },
        "plaintext_bytes": plaintext_bytes,
        "stored_bytes": consumed,
        "bytes_reclaimed": if dry_run { 0 } else { consumed },
    }))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    let ttl_days = option_f64(arguments, "--ttl-days", DEFAULT_TTL_DAYS)?;
    let dry_run = !arguments.iter().any(|value| value == "--apply");
    match command {
        [root, action] if root == "evidence" && action == "gc" => {
            collect(state_root, ttl_days, None, dry_run)
        }
        [root, action] if root == "maintenance" && action == "janitor" => {
            let maximum = option_i64(arguments, "--max-delete-bytes", DEFAULT_MAX_DELETE_BYTES)?;
            collect(state_root, ttl_days, Some(maximum), dry_run)
        }
        _ => Err("EVIDENCE_GC_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn evidence_collection_commands_are_supported() {
        assert!(supports(&["evidence".to_owned(), "gc".to_owned()]));
        assert!(supports(&["maintenance".to_owned(), "janitor".to_owned()]));
    }
}
