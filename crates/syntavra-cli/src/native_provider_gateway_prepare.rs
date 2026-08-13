#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

use super::super::native_evidence_store::NativeEvidenceStore;

const PROVIDER_DB: &str = "provider-gateway.sqlite3";
const SCHEMA_VERSION: i64 = 1;

const CREDENTIAL_KEYS: &[&str] = &[
    "authorization",
    "api-key",
    "apikey",
    "x-api-key",
    "openai-api-key",
    "anthropic-api-key",
    "google-api-key",
    "access-token",
    "bearer-token",
];

const REQUEST_VOLATILE_KEYS: &[&str] = &[
    "request_id",
    "client_request_id",
    "trace_id",
    "span_id",
    "timestamp",
    "created_at",
    "updated_at",
    "idempotency_key",
];

const PROMPT_VOLATILE_KEYS: &[&str] = &[
    "timestamp",
    "request_id",
    "trace_id",
    "nonce",
    "usage",
    "cost",
    "latency_ms",
];

const ALIGN_VOLATILE_KEYS: &[&str] = &[
    "created_at",
    "updated_at",
    "timestamp",
    "request_id",
    "response_id",
    "trace_id",
    "span_id",
    "latency_ms",
    "duration_ms",
    "usage",
    "cost",
];

#[derive(Clone, Copy)]
struct ProviderCapabilities {
    provider: &'static str,
    family: &'static str,
}

struct CachePlanInfo {
    expires_at: f64,
    refresh_after: f64,
    cacheable_tokens: i64,
    volatile_tokens: i64,
    reordered: bool,
}

pub(super) fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("PROVIDER_PREPARE_CLOCK_FAILED:{error}"))
}

fn canonical_write(value: &Value, output: &mut Vec<u8>) -> Result<(), String> {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {
            output.extend_from_slice(
                &serde_json::to_vec(value)
                    .map_err(|error| format!("PROVIDER_CANONICAL_JSON_FAILED:{error}"))?,
            );
        }
        Value::Array(rows) => {
            output.push(b'[');
            for (index, row) in rows.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                canonical_write(row, output)?;
            }
            output.push(b']');
        }
        Value::Object(map) => {
            output.push(b'{');
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort();
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                output.extend_from_slice(
                    &serde_json::to_vec(key)
                        .map_err(|error| format!("PROVIDER_CANONICAL_KEY_FAILED:{error}"))?,
                );
                output.push(b':');
                canonical_write(&map[key], output)?;
            }
            output.push(b'}');
        }
    }
    Ok(())
}

pub(super) fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    canonical_write(value, &mut output)?;
    Ok(output)
}

fn digest(value: &Value) -> Result<String, String> {
    Ok(sha256_hex(&canonical_json(value)?))
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64().is_some_and(|number| number != 0.0),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn py_string(value: Option<&Value>, default: &str) -> String {
    match value {
        None | Some(Value::Null) => default.to_owned(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Bool(value)) => if *value { "True" } else { "False" }.to_owned(),
        Some(Value::Number(value)) => value.to_string(),
        Some(value) => serde_json::to_string(value).unwrap_or_else(|_| default.to_owned()),
    }
}

pub(super) fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
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

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .unwrap_or(Ok(default))
}

fn provider_name(arguments: &[String]) -> Result<String, String> {
    arguments
        .windows(2)
        .position(|pair| pair[0] == "provider" && pair[1] == "prepare")
        .and_then(|index| arguments.get(index + 2))
        .filter(|value| !value.starts_with('-'))
        .cloned()
        .ok_or_else(|| "PROVIDER_PREPARE_PROVIDER_MISSING".to_owned())
}

fn capabilities(provider: &str) -> Result<ProviderCapabilities, String> {
    match provider.trim().to_lowercase().as_str() {
        "openai" | "chatgpt" | "responses" | "azure-openai" => Ok(ProviderCapabilities {
            provider: "openai",
            family: "openai",
        }),
        "anthropic" | "claude" | "bedrock-anthropic" | "vertex-anthropic" => {
            Ok(ProviderCapabilities {
                provider: "anthropic",
                family: "anthropic",
            })
        }
        "gemini" | "google" | "google-ai" | "vertex-gemini" => Ok(ProviderCapabilities {
            provider: "gemini",
            family: "gemini",
        }),
        "openrouter" | "litellm" | "vllm" | "ollama" | "lmstudio" | "openai-compatible" => {
            Ok(ProviderCapabilities {
                provider: "openai-compatible",
                family: "openai",
            })
        }
        _ => Err(format!("unsupported provider: {provider}")),
    }
}

fn reject_credentials(value: &Value, path: &str) -> Result<(), String> {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                let normalized = key.to_lowercase().replace('_', "-");
                if CREDENTIAL_KEYS.contains(&normalized.as_str()) {
                    return Err(format!("credential field is transport-only: {path}.{key}"));
                }
                reject_credentials(child, &format!("{path}.{key}"))?;
            }
        }
        Value::Array(rows) => {
            for (index, child) in rows.iter().enumerate() {
                reject_credentials(child, &format!("{path}[{index}]"))?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn clean_mapping(value: &Value, volatile: &[&str]) -> Value {
    match value {
        Value::Object(map) => {
            let mut output = Map::new();
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort();
            for key in keys {
                if volatile.contains(&key.as_str()) || key.starts_with('_') {
                    continue;
                }
                output.insert(key.clone(), clean_mapping(&map[key], volatile));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(
            rows.iter()
                .map(|row| clean_mapping(row, volatile))
                .collect(),
        ),
        _ => value.clone(),
    }
}

fn stable_message(message: &Value) -> bool {
    let Some(map) = message.as_object() else {
        return false;
    };
    let role = map
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    if matches!(role.as_str(), "system" | "developer") {
        return true;
    }
    if role == "tool" && map.get("cache_control") == Some(&Value::String("stable".to_owned())) {
        return true;
    }
    truthy(map.get("stable")) || truthy(map.get("cacheable"))
}

fn message_sequence(request: &Value, family: &str) -> Vec<Value> {
    let Some(map) = request.as_object() else {
        return Vec::new();
    };
    let source = if family == "gemini" {
        map.get("contents")
    } else {
        map.get("messages")
            .filter(|value| truthy(Some(value)))
            .or_else(|| map.get("input").filter(|value| truthy(Some(value))))
    };
    match source {
        Some(Value::String(text)) if family != "gemini" => {
            vec![json!({"role": "user", "content": text})]
        }
        Some(Value::Array(rows)) => rows.iter().filter(|row| row.is_object()).cloned().collect(),
        _ => Vec::new(),
    }
}

fn atomic_write(path: &Path, payload: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "PROVIDER_ATOMIC_PARENT_INVALID".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| format!("PROVIDER_ATOMIC_CREATE_FAILED:{error}"))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("state"),
        std::process::id()
    ));
    let result = (|| -> std::io::Result<()> {
        let mut output = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temporary)?;
        output.write_all(payload)?;
        output.flush()?;
        output.sync_all()?;
        #[cfg(windows)]
        if path.exists() {
            fs::remove_file(path)?;
        }
        fs::rename(&temporary, path)?;
        Ok(())
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("PROVIDER_ATOMIC_WRITE_FAILED:{error}"));
    }
    Ok(())
}

fn save_cache_plan(state_root: &Path, key: &str, plan: Value) -> Result<(), String> {
    let path = state_root.join("cache").join("plans.json");
    let mut current = if path.is_file() {
        serde_json::from_slice::<Value>(
            &fs::read(&path).map_err(|error| format!("PROVIDER_CACHE_PLAN_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("PROVIDER_CACHE_PLAN_INVALID:{error}"))?
    } else {
        json!({})
    };
    if !current.is_object() {
        current = json!({});
    }
    let object = current.as_object_mut().expect("object");
    if !object.get("plans").is_some_and(Value::is_object) {
        object.insert("plans".to_owned(), json!({}));
    }
    object
        .get_mut("plans")
        .and_then(Value::as_object_mut)
        .expect("plans")
        .insert(key.to_owned(), plan);
    object.insert("updated_at".to_owned(), Value::from(now_seconds()?));
    let mut payload = canonical_json(&current)?;
    payload.push(b'\n');
    atomic_write(&path, &payload)
}

fn cache_plan(
    state_root: &Path,
    messages: &[Value],
    provider: &str,
    model: &str,
    ttl_seconds: i64,
    reorder: bool,
) -> Result<(CachePlanInfo, Vec<Value>), String> {
    let now = now_seconds()?;
    let ttl = ttl_seconds.max(1);
    let stable_rows = messages
        .iter()
        .filter(|row| stable_message(row))
        .cloned()
        .collect::<Vec<_>>();
    let volatile_rows = messages
        .iter()
        .filter(|row| !stable_message(row))
        .cloned()
        .collect::<Vec<_>>();
    let ordered = if reorder {
        stable_rows
            .iter()
            .chain(volatile_rows.iter())
            .cloned()
            .collect::<Vec<_>>()
    } else {
        messages.to_vec()
    };
    let stable_prefix = ordered
        .iter()
        .take(stable_rows.len())
        .map(|row| clean_mapping(row, PROMPT_VOLATILE_KEYS))
        .collect::<Vec<_>>();
    let stable_prefix_hash = digest(&Value::Array(stable_prefix))?;
    let mut segments = Vec::<Value>::new();
    let mut cacheable_tokens = 0_i64;
    let mut volatile_tokens = 0_i64;
    for row in &ordered {
        let clean = clean_mapping(row, PROMPT_VOLATILE_KEYS);
        let raw = canonical_json(&clean)?;
        let stable = stable_message(row);
        let tokens = i64::try_from((raw.len() / 4).max(1))
            .map_err(|_| "PROVIDER_CACHE_TOKEN_COUNT_INVALID".to_owned())?;
        if stable {
            cacheable_tokens += tokens;
        } else {
            volatile_tokens += tokens;
        }
        let role = row
            .as_object()
            .map(|map| py_string(map.get("role"), "unknown"))
            .unwrap_or_else(|| "unknown".to_owned());
        segments.push(json!({
            "role": role,
            "stable": stable,
            "bytes": raw.len(),
            "tokens_estimate": tokens,
            "content_hash": sha256_hex(&raw),
            "reason": if stable { "stable-prefix" } else { "volatile-tail" },
        }));
    }
    let reordered = reorder && ordered != messages;
    let expires_at = now + ttl as f64;
    let refresh_after = now + ttl as f64 * 0.75;
    let plan = json!({
        "provider": provider,
        "model": model,
        "stable_prefix_hash": stable_prefix_hash,
        "stable_messages": stable_rows.len(),
        "volatile_messages": volatile_rows.len(),
        "cacheable_tokens": cacheable_tokens,
        "volatile_tokens": volatile_tokens,
        "ttl_seconds": ttl,
        "expires_at": expires_at,
        "refresh_after": refresh_after,
        "reordered": reordered,
        "segments": segments,
    });
    save_cache_plan(
        state_root,
        &format!(
            "{provider}:{model}:{}",
            plan["stable_prefix_hash"].as_str().unwrap_or_default()
        ),
        plan,
    )?;
    Ok((
        CachePlanInfo {
            expires_at,
            refresh_after,
            cacheable_tokens,
            volatile_tokens,
            reordered,
        },
        ordered,
    ))
}

fn safe_reorder(prepared: &mut Value, family: &str, ordered: &[Value], original: &[Value]) -> bool {
    if ordered == original || family == "gemini" {
        return false;
    }
    let stable = ordered
        .iter()
        .filter(|row| stable_message(row))
        .collect::<Vec<_>>();
    if stable.is_empty()
        || stable.iter().any(|row| {
            !matches!(
                row.as_object()
                    .and_then(|map| map.get("role"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_lowercase()
                    .as_str(),
                "system" | "developer"
            )
        })
    {
        return false;
    }
    let Some(map) = prepared.as_object_mut() else {
        return false;
    };
    let key = if map.get("messages").is_some_and(Value::is_array) {
        "messages"
    } else {
        "input"
    };
    if !map.get(key).is_some_and(Value::is_array) {
        return false;
    }
    map.insert(key.to_owned(), Value::Array(ordered.to_vec()));
    true
}

fn align(messages: &[Value]) -> Result<(String, usize), String> {
    let keep_tail = usize::from(!messages.is_empty());
    let stable_count = messages.len().saturating_sub(keep_tail);
    let stable = messages
        .iter()
        .take(stable_count)
        .map(|row| clean_mapping(row, ALIGN_VOLATILE_KEYS))
        .collect::<Vec<_>>();
    Ok((digest(&Value::Array(stable))?, stable_count))
}

fn unique_push(rows: &mut Vec<String>, value: impl Into<String>) {
    let value = value.into();
    if !rows.contains(&value) {
        rows.push(value);
    }
}

fn apply_anthropic_cache_control(request: &mut Value, ttl_seconds: i64) -> bool {
    let marker = if ttl_seconds >= 3600 {
        json!({"type": "ephemeral", "ttl": "1h"})
    } else {
        json!({"type": "ephemeral"})
    };
    let Some(map) = request.as_object_mut() else {
        return false;
    };
    if let Some(Value::String(system)) = map.get("system") {
        if !system.is_empty() {
            let text = system.clone();
            map.insert(
                "system".to_owned(),
                json!([{"type": "text", "text": text, "cache_control": marker}]),
            );
            return true;
        }
    }
    if let Some(Value::Array(system)) = map.get("system") {
        if !system.is_empty() {
            let mut blocks = system.clone();
            for index in (0..blocks.len()).rev() {
                if let Some(block) = blocks[index].as_object_mut() {
                    block.insert("cache_control".to_owned(), marker.clone());
                    map.insert("system".to_owned(), Value::Array(blocks));
                    return true;
                }
            }
        }
    }
    if let Some(Value::Array(messages)) = map.get("messages") {
        if !messages.is_empty() {
            let mut prepared = messages.clone();
            if let Some(first) = prepared.first_mut().and_then(Value::as_object_mut) {
                match first.get("content").cloned() {
                    Some(Value::String(content)) => {
                        first.insert(
                            "content".to_owned(),
                            json!([{"type": "text", "text": content, "cache_control": marker}]),
                        );
                        map.insert("messages".to_owned(), Value::Array(prepared));
                        return true;
                    }
                    Some(Value::Array(content)) if !content.is_empty() => {
                        let mut blocks = content;
                        if let Some(last) = blocks.last_mut().and_then(Value::as_object_mut) {
                            last.insert("cache_control".to_owned(), marker);
                            first.insert("content".to_owned(), Value::Array(blocks));
                            map.insert("messages".to_owned(), Value::Array(prepared));
                            return true;
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    false
}

fn apply_prompt_cache(
    caps: ProviderCapabilities,
    request: &mut Value,
    cache_key: &str,
    ttl_seconds: i64,
    explicit_cache_name: &str,
) -> (String, Vec<String>) {
    let Some(map) = request.as_object_mut() else {
        return ("provider-cache-unavailable".to_owned(), Vec::new());
    };
    match caps.provider {
        "openai" => {
            if !map.contains_key("prompt_cache_key") {
                map.insert(
                    "prompt_cache_key".to_owned(),
                    Value::String(cache_key.chars().take(64).collect()),
                );
            }
            if ttl_seconds >= 86400 && !map.contains_key("prompt_cache_retention") {
                map.insert(
                    "prompt_cache_retention".to_owned(),
                    Value::String("24h".to_owned()),
                );
            }
            (
                "provider-explicit-key".to_owned(),
                vec!["openai-prompt-cache-key".to_owned()],
            )
        }
        "anthropic" => {
            let _ = map;
            if apply_anthropic_cache_control(request, ttl_seconds) {
                (
                    "provider-explicit-breakpoint".to_owned(),
                    vec!["anthropic-cache-control".to_owned()],
                )
            } else {
                (
                    "provider-cache-unavailable".to_owned(),
                    vec!["no-cacheable-anthropic-prefix".to_owned()],
                )
            }
        }
        "gemini" => {
            if explicit_cache_name.is_empty() {
                (
                    "provider-implicit-prefix".to_owned(),
                    vec!["gemini-implicit-cache-stable-prefix".to_owned()],
                )
            } else {
                map.insert(
                    "cachedContent".to_owned(),
                    Value::String(explicit_cache_name.to_owned()),
                );
                (
                    "provider-explicit-resource".to_owned(),
                    vec!["gemini-cached-content".to_owned()],
                )
            }
        }
        _ => (
            "stable-prefix-only".to_owned(),
            vec!["provider-has-no-declared-prompt-cache-control".to_owned()],
        ),
    }
}

fn has_tools(request: &Value) -> bool {
    let Some(map) = request.as_object() else {
        return false;
    };
    let tools = map
        .get("tools")
        .filter(|value| truthy(Some(value)))
        .or_else(|| map.get("functions").filter(|value| truthy(Some(value))));
    if tools.is_none() {
        return false;
    }
    let choice = map
        .get("tool_choice")
        .filter(|value| truthy(Some(value)))
        .or_else(|| map.get("toolConfig").filter(|value| truthy(Some(value))))
        .or_else(|| map.get("tool_config").filter(|value| truthy(Some(value))));
    !matches!(choice, Some(Value::String(value)) if value == "none")
        && choice != Some(&json!({"type": "none"}))
        && choice != Some(&json!({"function_calling_config": {"mode": "NONE"}}))
}

fn is_streaming(request: &Value) -> bool {
    request
        .as_object()
        .is_some_and(|map| truthy(map.get("stream")) || truthy(map.get("streaming")))
}

fn temperature(request: &Value) -> f64 {
    let Some(map) = request.as_object() else {
        return 0.0;
    };
    let raw = map
        .get("temperature")
        .or_else(|| {
            map.get("generationConfig")
                .and_then(Value::as_object)
                .and_then(|row| row.get("temperature"))
        })
        .or_else(|| {
            map.get("generation_config")
                .and_then(Value::as_object)
                .and_then(|row| row.get("temperature"))
        });
    match raw {
        None | Some(Value::Null) => 0.0,
        Some(Value::Bool(value)) => {
            if *value {
                1.0
            } else {
                0.0
            }
        }
        Some(Value::Number(value)) => value.as_f64().unwrap_or(1.0),
        Some(Value::String(value)) => value.parse::<f64>().unwrap_or(1.0),
        _ => 1.0,
    }
}

pub(super) fn initialize_gateway(state_root: &Path) -> Result<Connection, String> {
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
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                argv_json TEXT NOT NULL,
                cwd TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                pid INTEGER,
                exit_code INTEGER,
                timed_out INTEGER NOT NULL DEFAULT 0,
                cancelled INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                evidence_handle TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                timeout_seconds REAL NOT NULL DEFAULT 0,
                stdout_path TEXT NOT NULL DEFAULT '',
                stderr_path TEXT NOT NULL DEFAULT '',
                repository_tree TEXT NOT NULL DEFAULT 'unknown',
                environment_hash TEXT NOT NULL DEFAULT 'unknown',
                project_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at DESC);
            CREATE TABLE IF NOT EXISTS completion_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                exit_code INTEGER,
                completed_at REAL NOT NULL,
                evidence_handle TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            );
            CREATE TABLE IF NOT EXISTS verifier_results(
                cache_key TEXT PRIMARY KEY,
                command_json TEXT NOT NULL,
                tree_hash TEXT NOT NULL,
                environment_hash TEXT NOT NULL,
                dependency_hash TEXT NOT NULL,
                toolchain_hash TEXT NOT NULL,
                success INTEGER NOT NULL,
                exit_code INTEGER NOT NULL,
                evidence_handle TEXT NOT NULL,
                affected_paths_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            INSERT INTO metadata(key,value) VALUES('schema_version','2')
                ON CONFLICT(key) DO UPDATE SET value=excluded.value;
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

pub(super) fn initialize_usage_ledger(state_root: &Path) -> Result<(), String> {
    let connection = Connection::open(state_root.join("usage-receipts.sqlite3"))
        .map_err(|error| format!("PROVIDER_USAGE_DB_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            r#"
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=30000;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS usage_receipt_ledger(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                arm_id TEXT NOT NULL,
                repetition INTEGER NOT NULL,
                cache_mode TEXT NOT NULL,
                provider TEXT NOT NULL,
                request_id_hash TEXT NOT NULL,
                provider_response_hash TEXT NOT NULL,
                fresh_input_tokens INTEGER NOT NULL,
                cached_input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                reasoning_tokens INTEGER NOT NULL,
                quota_cost REAL NOT NULL,
                hardware_hash TEXT NOT NULL,
                receipt_hash TEXT NOT NULL,
                previous_chain_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL UNIQUE,
                signature_mode TEXT NOT NULL,
                signature TEXT NOT NULL,
                raw_usage_hash TEXT NOT NULL,
                raw_usage_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(task_id,arm_id,repetition,cache_mode,request_id_hash)
            );
            CREATE INDEX IF NOT EXISTS usage_receipt_identity_idx
                ON usage_receipt_ledger(task_id,arm_id,repetition,cache_mode);
            CREATE TABLE IF NOT EXISTS token_attribution_receipts(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                arm_id TEXT NOT NULL,
                repetition INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                request_id_hash TEXT NOT NULL,
                provider_receipt_hash TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                confidence_json TEXT NOT NULL,
                baseline_tokens INTEGER,
                baseline_confidence TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                receipt_hash TEXT NOT NULL UNIQUE,
                UNIQUE(task_id,arm_id,repetition,request_id_hash)
            );
            CREATE INDEX IF NOT EXISTS token_attribution_session_idx
                ON token_attribution_receipts(session_id,created_at);
            CREATE INDEX IF NOT EXISTS token_attribution_task_idx
                ON token_attribution_receipts(task_id,arm_id,repetition);
            "#,
        )
        .map_err(|error| format!("PROVIDER_USAGE_SCHEMA_FAILED:{error}"))?;
    Ok(())
}
fn replay_lookup(state_root: &Path, cache_key: &str) -> Result<String, String> {
    let mut connection = initialize_gateway(state_root)?;
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

pub(super) fn project_root(arguments: &[String]) -> Result<PathBuf, String> {
    let raw = option_value(arguments, "--project")?.unwrap_or_else(|| ".".to_owned());
    fs::canonicalize(raw).map_err(|error| format!("PROVIDER_PROJECT_RESOLVE_FAILED:{error}"))
}

pub(super) fn stable_project_id(project: &Path) -> Result<String, String> {
    let raw = project
        .to_str()
        .ok_or_else(|| "PROVIDER_PROJECT_UTF8_INVALID".to_owned())?;
    let normalized = if cfg!(windows) {
        let mut value = raw.to_owned();
        if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
            value = format!(r"\\{rest}");
        } else if let Some(rest) = value.strip_prefix(r"\\?\") {
            value = rest.to_owned();
        }
        value.replace('/', "\\").to_lowercase()
    } else {
        raw.to_owned()
    };
    Ok(sha256_hex(normalized.as_bytes()))
}

fn read_request(arguments: &[String]) -> Result<Value, String> {
    let inline = option_value(arguments, "--request")?.unwrap_or_default();
    let raw = match option_value(arguments, "--input")? {
        Some(path) if path == "-" => {
            let mut value = String::new();
            std::io::stdin()
                .read_to_string(&mut value)
                .map_err(|error| format!("PROVIDER_REQUEST_STDIN_FAILED:{error}"))?;
            value
        }
        Some(path) => fs::read_to_string(path)
            .map_err(|error| format!("PROVIDER_REQUEST_READ_FAILED:{error}"))?,
        None => inline,
    };
    if raw.trim().is_empty() {
        return Ok(json!({}));
    }
    let value = serde_json::from_str::<Value>(&raw)
        .map_err(|error| format!("PROVIDER_REQUEST_JSON_INVALID:{error}"))?;
    if !value.is_object() {
        return Err("provider request must be a JSON object".to_owned());
    }
    Ok(value)
}

pub(super) fn output_value(value: &Value, arguments: &[String]) -> Result<Value, String> {
    let Some(path) = option_value(arguments, "--output")? else {
        return Ok(value.clone());
    };
    let target = PathBuf::from(path);
    if let Some(parent) = target.parent().filter(|path| !path.as_os_str().is_empty()) {
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

fn cache_identity(provider: &str, model: &str) -> Value {
    json!({
        "provider": provider.to_lowercase(),
        "model": model,
        "revision": env::var("SYNTAVRA_MODEL_REVISION").unwrap_or_else(|_| "unknown".to_owned()),
        "endpoint_version": env::var("SYNTAVRA_ENDPOINT_VERSION").unwrap_or_default(),
        "region": env::var("SYNTAVRA_PROVIDER_REGION").unwrap_or_default(),
        "system_fingerprint": "",
        "tool_implementation_hash": env::var("SYNTAVRA_TOOL_IMPLEMENTATION_HASH").unwrap_or_default(),
        "security_policy_hash": env::var("SYNTAVRA_SECURITY_POLICY_HASH").unwrap_or_default(),
        "runtime_version": env::var("SYNTAVRA_RUNTIME_VERSION").unwrap_or_else(|_| "0.0.1".to_owned()),
    })
}

fn prepare_model(model_option: &str, request: &Value) -> String {
    if !model_option.is_empty() {
        return model_option.to_owned();
    }
    request
        .as_object()
        .and_then(|map| map.get("model"))
        .filter(|value| truthy(Some(value)))
        .map(|value| py_string(Some(value), "unknown"))
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}

fn prepare_impl(state_root: &Path) -> Result<Value, String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();

    // Match competitive_cli._gateway: all durable state surfaces exist before
    // request parsing/provider validation, including failure paths.
    let project = project_root(&arguments)?;
    let project_id = stable_project_id(&project)?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    initialize_usage_ledger(state_root)?;
    let _gateway_bootstrap = initialize_gateway(state_root)?;

    let provider_raw = provider_name(&arguments)?;
    let model_option = option_value(&arguments, "--model")?.unwrap_or_default();
    let request = read_request(&arguments)?;
    let resolved_model = prepare_model(&model_option, &request);

    // provider_extension.install() wraps ProviderGateway.prepare in Python.
    // The identity participates in request/evidence hashes but is removed from
    // the public prepared_request returned to the caller.
    let identity = cache_identity(&provider_raw, &resolved_model);
    let identity_fingerprint = digest(&identity)?;
    let mut request_with_identity = request.clone();
    request_with_identity
        .as_object_mut()
        .ok_or_else(|| "provider request must be a JSON object".to_owned())?
        .insert("syntavra_cache_identity".to_owned(), identity);

    let caps = capabilities(&provider_raw)?;
    let cache_policy =
        option_value(&arguments, "--cache-policy")?.unwrap_or_else(|| "auto".to_owned());
    if !matches!(
        cache_policy.as_str(),
        "off" | "auto" | "read" | "read-write"
    ) {
        return Err("cache_policy must be off, auto, read, or read-write".to_owned());
    }
    let replay_ttl = integer_option(&arguments, "--replay-ttl-seconds", 900)?;
    if replay_ttl < 1 {
        return Err("replay_ttl_seconds must be positive".to_owned());
    }
    let prompt_ttl = integer_option(&arguments, "--prompt-cache-ttl-seconds", 300)?;
    let explicit_cache_name =
        option_value(&arguments, "--explicit-cache-name")?.unwrap_or_default();
    let allow_tool_replay = arguments.iter().any(|value| value == "--allow-tool-replay");
    reject_credentials(&request_with_identity, "request")?;

    let mut prepared = request_with_identity.clone();
    let mut messages = message_sequence(&prepared, caps.family);
    let (plan, ordered) = cache_plan(
        state_root,
        &messages,
        caps.provider,
        &resolved_model,
        prompt_ttl.max(1),
        cache_policy != "off",
    )?;
    let mut cache_reordered = false;
    if cache_policy != "off" && plan.reordered {
        cache_reordered = safe_reorder(&mut prepared, caps.family, &ordered, &messages);
        if cache_reordered {
            messages = message_sequence(&prepared, caps.family);
        }
    }
    let (stable_prefix_hash, stable_message_count) = align(&messages)?;
    let stable_request = clean_mapping(&prepared, REQUEST_VOLATILE_KEYS);
    let request_hash = digest(&stable_request)?;
    let cache_key = digest(&json!({
        "schema": SCHEMA_VERSION,
        "provider": caps.provider,
        "model": resolved_model,
        "request": stable_request,
    }))?;

    let mut reasons = Vec::<String>::new();
    if cache_reordered {
        unique_push(&mut reasons, "stable-prefix-layout-applied");
    } else if plan.reordered {
        unique_push(
            &mut reasons,
            "stable-prefix-layout-proposed-not-safe-to-reorder",
        );
    }
    if cache_policy != "off" {
        unique_push(
            &mut reasons,
            format!("cache-refresh-after:{}", plan.refresh_after as i64),
        );
        unique_push(
            &mut reasons,
            format!("cache-expires-at:{}", plan.expires_at as i64),
        );
    }
    let mut prompt_cache_mode = "disabled".to_owned();
    if cache_policy != "off" {
        let (mode, cache_reasons) = apply_prompt_cache(
            caps,
            &mut prepared,
            &cache_key,
            prompt_ttl.max(0),
            &explicit_cache_name,
        );
        prompt_cache_mode = mode;
        for reason in cache_reasons {
            unique_push(&mut reasons, reason);
        }
    }

    let tools = has_tools(&prepared);
    let stream = is_streaming(&prepared);
    let deterministic = temperature(&prepared) <= 0.0;
    let replay_cacheable =
        cache_policy != "off" && deterministic && !stream && (allow_tool_replay || !tools);
    if !deterministic {
        unique_push(&mut reasons, "response-replay-disabled-temperature");
    }
    if stream {
        unique_push(&mut reasons, "response-replay-disabled-stream");
    }
    if tools && !allow_tool_replay {
        unique_push(&mut reasons, "response-replay-disabled-tools");
    }

    let request_bytes = canonical_json(&request_with_identity)?;
    let request_handle = evidence.put(
        &request_bytes,
        "provider-request",
        &json!({
            "provider": caps.provider,
            "model": resolved_model,
            "request_hash": request_hash,
            "cache_key": cache_key,
        }),
    )?;
    let replay_handle =
        if replay_cacheable && matches!(cache_policy.as_str(), "auto" | "read" | "read-write") {
            replay_lookup(state_root, &cache_key)?
        } else {
            String::new()
        };
    if !replay_handle.is_empty() {
        unique_push(&mut reasons, "exact-response-replay-hit");
    }

    let mut connection = initialize_gateway(state_root)?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("PROVIDER_AUDIT_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO provider_request_audit(provider,model,request_hash,cache_key,request_handle,prompt_cache_mode,replay_cacheable,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
            (
                caps.provider,
                resolved_model.as_str(),
                request_hash.as_str(),
                cache_key.as_str(),
                request_handle.as_str(),
                prompt_cache_mode.as_str(),
                i64::from(replay_cacheable),
                now_seconds()?,
            ),
        )
        .map_err(|error| format!("PROVIDER_AUDIT_INSERT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("PROVIDER_AUDIT_COMMIT_FAILED:{error}"))?;

    prepared
        .as_object_mut()
        .ok_or_else(|| "PROVIDER_PREPARED_REQUEST_INVALID".to_owned())?
        .remove("syntavra_cache_identity");
    unique_push(
        &mut reasons,
        format!("cache-identity:{identity_fingerprint}"),
    );

    let value = json!({
        "provider": caps.provider,
        "model": resolved_model,
        "request_hash": request_hash,
        "cache_key": cache_key,
        "request_handle": request_handle,
        "stable_prefix_hash": stable_prefix_hash,
        "stable_message_count": stable_message_count,
        "prompt_cache_mode": prompt_cache_mode,
        "replay_cacheable": replay_cacheable,
        "replay_hit": !replay_handle.is_empty(),
        "replay_response_handle": replay_handle,
        "prepared_request": prepared,
        "reasons": reasons,
        "cache_expires_at": if cache_policy != "off" { plan.expires_at } else { 0.0 },
        "cache_refresh_after": if cache_policy != "off" { plan.refresh_after } else { 0.0 },
        "cacheable_tokens": if cache_policy != "off" { plan.cacheable_tokens } else { 0 },
        "volatile_tokens": plan.volatile_tokens,
        "cache_reordered": cache_reordered,
    });
    output_value(&value, &arguments)
}

pub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {
    prepare_impl(state_root)
}
