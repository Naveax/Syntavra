#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OptionalExtension, TransactionBehavior};
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

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let value = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(value) = value {
            if found.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            found = Some(value);
        }
        index += 1;
    }
    Ok(found)
}

fn process_arguments() -> Vec<String> {
    std::env::args().skip(1).collect()
}

fn apply_output_with_arguments(value: &Value, arguments: &[String]) -> Result<Value, String> {
    let Some(path) = option_value(arguments, "--output")? else {
        return Ok(value.clone());
    };
    let target = PathBuf::from(path);
    if let Some(parent) = target
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)
            .map_err(|error| format!("PROVIDER_OUTPUT_CREATE_FAILED:{error}"))?;
    }
    let pretty = serde_json::to_string_pretty(value)
        .map_err(|error| format!("PROVIDER_OUTPUT_SERIALIZE_FAILED:{error}"))?;
    let rendered = if cfg!(windows) {
        format!("{}\r\n", pretty.replace('\n', "\r\n"))
    } else {
        format!("{pretty}\n")
    };
    fs::write(&target, rendered.as_bytes())
        .map_err(|error| format!("PROVIDER_OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "output": target.display().to_string(),
        "bytes": rendered.len(),
    }))
}

fn apply_output(value: &Value) -> Result<Value, String> {
    apply_output_with_arguments(value, &process_arguments())
}

fn emit_and_exit(value: &Value, code: u8) -> ! {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    std::process::exit(i32::from(code));
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

    apply_output(&json!({
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

fn evidence_json(state_root: &Path, handle: &str) -> Result<Value, String> {
    let project_id = evidence_project_id(state_root, handle)?;
    let payload = NativeEvidenceStore::open(state_root, &project_id)?.get(handle)?;
    serde_json::from_slice(&payload)
        .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_JSON_INVALID:{error}"))
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

    let result = json!({
        "ok": reasons.is_empty() && database_integrity,
        "entries": entries,
        "reasons": reasons,
        "database_integrity": database_integrity,
    });
    let rendered = apply_output(&result)?;
    if result["ok"] != true {
        emit_and_exit(&rendered, 3);
    }
    Ok(rendered)
}

fn replay_handle_for_cache_key(state_root: &Path, cache_key: &str) -> Result<String, String> {
    let mut connection = initialize(state_root)?;
    let now = now_seconds()?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_TRANSACTION_FAILED:{error}"))?;
    let handle = transaction
        .query_row(
            "SELECT response_handle FROM provider_response_cache WHERE cache_key=?1 AND expires_at>?2",
            (cache_key, now),
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_LOOKUP_FAILED:{error}"))?;
    if handle.is_none() {
        transaction
            .execute(
                "DELETE FROM provider_response_cache WHERE expires_at<=?1",
                [now],
            )
            .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_EXPIRE_FAILED:{error}"))?;
    } else {
        transaction
            .execute(
                "UPDATE provider_response_cache SET hit_count=hit_count+1,last_hit_at=?1 WHERE cache_key=?2",
                (now, cache_key),
            )
            .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_HIT_UPDATE_FAILED:{error}"))?;
    }
    transaction
        .commit()
        .map_err(|error| format!("PROVIDER_GATEWAY_REPLAY_COMMIT_FAILED:{error}"))?;
    Ok(handle.unwrap_or_default())
}

fn replay_target(arguments: &[String]) -> Result<(String, bool), String> {
    let plan = option_value(arguments, "--plan")?;
    let cache_key = option_value(arguments, "--cache-key")?;
    match (plan, cache_key) {
        (Some(_), Some(_)) => Err("PROVIDER_REPLAY_TARGET_CONFLICT".to_owned()),
        (None, None) => Err("PROVIDER_REPLAY_TARGET_MISSING".to_owned()),
        (None, Some(key)) => Ok((key, false)),
        (Some(path), None) => {
            let value = serde_json::from_slice::<Value>(
                &fs::read(path)
                    .map_err(|error| format!("PROVIDER_REPLAY_PLAN_READ_FAILED:{error}"))?,
            )
            .map_err(|error| format!("PROVIDER_REPLAY_PLAN_INVALID:{error}"))?;
            let object = value
                .as_object()
                .ok_or_else(|| "PROVIDER_REPLAY_PLAN_NOT_OBJECT".to_owned())?;
            let direct = object
                .get("replay_response_handle")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if !direct.is_empty() {
                return Ok((direct.to_owned(), true));
            }
            let key = object
                .get("cache_key")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| "PROVIDER_REPLAY_PLAN_CACHE_KEY_MISSING".to_owned())?;
            Ok((key.to_owned(), false))
        }
    }
}

pub(crate) fn replay(state_root: &Path) -> Result<Value, String> {
    let arguments = process_arguments();
    let (target, direct_handle) = replay_target(&arguments)?;
    let handle = if direct_handle {
        target
    } else {
        replay_handle_for_cache_key(state_root, &target)?
    };
    if handle.is_empty() {
        let rendered = apply_output_with_arguments(&json!({"hit": false}), &arguments)?;
        emit_and_exit(&rendered, 4);
    }
    let response = evidence_json(state_root, &handle)?;
    apply_output_with_arguments(&response, &arguments)
}
