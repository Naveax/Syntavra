#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_provider_gateway_prepare.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    value, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return value


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "use std::collections::{BTreeMap, BTreeSet};\n",
        "",
        "unused-collections-import",
    )

    text = replace_once(
        text,
        """    match raw {\n        None | Some(Value::Null) => 0.0,\n        Some(Value::Number(value)) => value.as_f64().unwrap_or(1.0),\n        Some(Value::String(value)) => value.parse::<f64>().unwrap_or(1.0),\n        _ => 1.0,\n    }\n""",
        """    match raw {\n        None | Some(Value::Null) => 0.0,\n        Some(Value::Bool(value)) => if *value { 1.0 } else { 0.0 },\n        Some(Value::Number(value)) => value.as_f64().unwrap_or(1.0),\n        Some(Value::String(value)) => value.parse::<f64>().unwrap_or(1.0),\n        _ => 1.0,\n    }\n""",
        "python-bool-temperature",
    )

    gateway = r'''fn initialize_gateway(state_root: &Path) -> Result<Connection, String> {
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

fn initialize_usage_ledger(state_root: &Path) -> Result<(), String> {
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
'''
    text = regex_once(
        text,
        r"fn initialize_gateway\(state_root: &Path\) -> Result<Connection, String> \{.*?\n\}\n\n(?=fn replay_lookup)",
        gateway,
        "state-and-usage-schema",
    )

    helpers = r'''fn cache_identity(provider: &str, model: &str) -> Value {
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

'''
    text = replace_once(
        text,
        "pub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {\n",
        helpers + "pub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {\n",
        "prepare-helpers",
    )

    prepare = r'''pub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {
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
    let cache_policy = option_value(&arguments, "--cache-policy")?
        .unwrap_or_else(|| "auto".to_owned());
    if !matches!(cache_policy.as_str(), "off" | "auto" | "read" | "read-write") {
        return Err("cache_policy must be off, auto, read, or read-write".to_owned());
    }
    let replay_ttl = integer_option(&arguments, "--replay-ttl-seconds", 900)?;
    if replay_ttl < 1 {
        return Err("replay_ttl_seconds must be positive".to_owned());
    }
    let prompt_ttl = integer_option(&arguments, "--prompt-cache-ttl-seconds", 300)?;
    let explicit_cache_name = option_value(&arguments, "--explicit-cache-name")?.unwrap_or_default();
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
        unique_push(&mut reasons, "stable-prefix-layout-proposed-not-safe-to-reorder");
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
    let replay_handle = if replay_cacheable
        && matches!(cache_policy.as_str(), "auto" | "read" | "read-write")
    {
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
'''
    text = regex_once(
        text,
        r"pub\(crate\) fn prepare\(state_root: &Path\) -> Result<Value, String> \{.*\Z",
        prepare,
        "prepare-body",
    )

    TARGET.write_text(text, encoding="utf-8")
    print(f"patched {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
