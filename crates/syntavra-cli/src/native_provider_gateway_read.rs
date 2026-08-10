#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::super::native_evidence_store::NativeEvidenceStore;

const PROVIDER_DB: &str = "provider-gateway.sqlite3";
const EVIDENCE_HANDLE_PREFIX: &str = "sc://sha256/";

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("PROVIDER_GATEWAY_CLOCK_FAILED:{error}"))
}

fn initialize(state_root: &Path) -> Result<Connection, String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("PROVIDER_GATEWAY_STATE_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(state_root.join(PROVIDER_DB))
        .map_err(|error| format!("PROVIDER_GATEWAY_DB_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            r#"
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=30000;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS provider_request_audit(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                request_handle TEXT NOT NULL,
                prompt_cache_mode TEXT NOT NULL,
                replay_cacheable INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS provider_request_hash_idx
                ON provider_request_audit(provider,model,request_hash);
            CREATE TABLE IF NOT EXISTS provider_response_cache(
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                request_handle TEXT NOT NULL,
                response_handle TEXT NOT NULL,
                response_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                last_hit_at REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS provider_cache_expiry_idx
                ON provider_response_cache(expires_at);
            "#,
        )
        .map_err(|error| format!("PROVIDER_GATEWAY_SCHEMA_FAILED:{error}"))?;
    Ok(connection)
}

fn integrity(connection: &Connection) -> Result<bool, String> {
    let result = connection
        .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
        .map_err(|error| format!("PROVIDER_GATEWAY_INTEGRITY_FAILED:{error}"))?;
    Ok(result == "ok")
}

pub(crate) fn stats(state_root: &Path) -> Result<Value, String> {
    let connection = initialize(state_root)?;
    let requests = connection
        .query_row("SELECT COUNT(*) FROM provider_request_audit", [], |row| {
            row.get::<_, i64>(0)
        })
        .map_err(|error| format!("PROVIDER_GATEWAY_REQUEST_COUNT_FAILED:{error}"))?;
    let now = now_seconds()?;
    let (cache_entries, replay_hits, active_cache_entries) = connection
        .query_row(
            "SELECT COUNT(*),COALESCE(SUM(hit_count),0),COALESCE(SUM(CASE WHEN expires_at>?1 THEN 1 ELSE 0 END),0) FROM provider_response_cache",
            [now],
            |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            },
        )
        .map_err(|error| format!("PROVIDER_GATEWAY_CACHE_STATS_FAILED:{error}"))?;

    let mut providers = BTreeMap::<String, i64>::new();
    let mut statement = connection
        .prepare("SELECT provider,COUNT(*) FROM provider_request_audit GROUP BY provider ORDER BY provider")
        .map_err(|error| format!("PROVIDER_GATEWAY_PROVIDER_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|error| format!("PROVIDER_GATEWAY_PROVIDER_QUERY_FAILED:{error}"))?;
    for row in rows {
        let (provider, count) =
            row.map_err(|error| format!("PROVIDER_GATEWAY_PROVIDER_ROW_FAILED:{error}"))?;
        providers.insert(provider, count);
    }

    Ok(json!({
        "requests": requests,
        "cache_entries": cache_entries,
        "replay_hits": replay_hits,
        "active_cache_entries": active_cache_entries,
        "providers": providers,
        "database_integrity": integrity(&connection)?,
    }))
}

fn evidence_project_id(state_root: &Path, handle: &str) -> Result<String, String> {
    let digest = handle
        .strip_prefix(EVIDENCE_HANDLE_PREFIX)
        .filter(|value| {
            value.len() == 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or_else(|| "PROVIDER_GATEWAY_EVIDENCE_HANDLE_INVALID".to_owned())?;
    let metadata_path = state_root
        .join("evidence")
        .join("metadata")
        .join(format!("{digest}.json"));
    let value = serde_json::from_slice::<Value>(
        &fs::read(metadata_path)
            .map_err(|error| format!("PROVIDER_GATEWAY_EVIDENCE_METADATA_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("PROVIDER_GATEWAY_EVIDENCE_METADATA_INVALID:{error}"))?;
    value["project_id"]
        .as_str()
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| "PROVIDER_GATEWAY_EVIDENCE_PROJECT_ID_MISSING".to_owned())
}

pub(crate) fn verify(state_root: &Path) -> Result<Value, String> {
    let connection = initialize(state_root)?;
    let database_integrity = integrity(&connection)?;
    let mut statement = connection
        .prepare(
            "SELECT cache_key,request_handle,response_handle,response_hash FROM provider_response_cache ORDER BY cache_key",
        )
        .map_err(|error| format!("PROVIDER_GATEWAY_VERIFY_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(|error| format!("PROVIDER_GATEWAY_VERIFY_QUERY_FAILED:{error}"))?;

    let mut entries = 0usize;
    let mut reasons = Vec::<String>::new();
    for row in rows {
        let (cache_key, request_handle, response_handle, expected_response_hash) =
            row.map_err(|error| format!("PROVIDER_GATEWAY_VERIFY_ROW_FAILED:{error}"))?;
        entries += 1;

        match evidence_project_id(state_root, &request_handle) {
            Ok(project_id) => match NativeEvidenceStore::open(state_root, &project_id)
                .and_then(|store| store.get(&request_handle))
            {
                Ok(_) => {}
                Err(_) => reasons.push(format!("request-evidence:{cache_key}")),
            },
            Err(_) => reasons.push(format!("request-evidence:{cache_key}")),
        }

        let response_project = match evidence_project_id(state_root, &response_handle) {
            Ok(value) => value,
            Err(_) => {
                reasons.push(format!("response-evidence:{cache_key}"));
                continue;
            }
        };
        let response = match NativeEvidenceStore::open(state_root, &response_project)
            .and_then(|store| store.get(&response_handle))
        {
            Ok(value) => value,
            Err(_) => {
                reasons.push(format!("response-evidence:{cache_key}"));
                continue;
            }
        };
        if sha256_hex(&response) != expected_response_hash {
            reasons.push(format!("response-hash:{cache_key}"));
        }
    }

    Ok(json!({
        "ok": reasons.is_empty() && database_integrity,
        "entries": entries,
        "reasons": reasons,
        "database_integrity": database_integrity,
    }))
}
