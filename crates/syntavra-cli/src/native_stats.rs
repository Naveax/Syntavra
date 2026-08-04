#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use rusqlite::{Connection, Row};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const MAX_ANALYTICS_BYTES: u64 = 64 * 1024 * 1024;
const ZERO_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";
const TOKEN_SOURCES: [&str; 12] = [
    "system",
    "skill_description",
    "skill_body",
    "tool_schema",
    "repository_context",
    "tool_output",
    "memory",
    "conversation_history",
    "user_prompt",
    "assistant_output",
    "reasoning",
    "cached",
];
const CONFIDENCE_LEVELS: [&str; 4] = [
    "PROVIDER_OBSERVED",
    "LOCALLY_TOKENIZED",
    "ESTIMATED",
    "UNKNOWN",
];

#[derive(Debug)]
struct UsageLedgerRow {
    sequence: i64,
    task_id: String,
    arm_id: String,
    repetition: i64,
    cache_mode: String,
    provider: String,
    request_id_hash: String,
    provider_response_hash: String,
    fresh_input_tokens: i64,
    cached_input_tokens: i64,
    output_tokens: i64,
    reasoning_tokens: i64,
    quota_cost: f64,
    hardware_hash: String,
    receipt_hash: String,
    previous_chain_hash: String,
    chain_hash: String,
    signature_mode: String,
    signature: String,
    raw_usage_hash: String,
    raw_usage_json: String,
    created_at: f64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "stats")
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[allow(clippy::cast_possible_truncation)]
fn truncated_i64(number: f64) -> Option<i64> {
    let truncated = number.trunc();
    if !truncated.is_finite()
        || !(-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0)
            .contains(&truncated)
    {
        return None;
    }
    Some(truncated as i64)
}

fn python_int(value: Option<&Value>) -> Result<i64, String> {
    match value {
        None => Ok(0),
        Some(Value::Bool(value)) => Ok(i64::from(*value)),
        Some(Value::Number(value)) => value
            .as_i64()
            .or_else(|| value.as_u64().and_then(|number| i64::try_from(number).ok()))
            .or_else(|| value.as_f64().and_then(truncated_i64))
            .ok_or_else(|| "ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(Value::String(value)) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| "ANALYTICS_INTEGER_INVALID".to_owned()),
        Some(_) => Err("ANALYTICS_INTEGER_INVALID".to_owned()),
    }
}

fn python_float(value: Option<&Value>) -> Result<f64, String> {
    let number = match value {
        None => 0.0,
        Some(Value::Bool(value)) => f64::from(u8::from(*value)),
        Some(Value::Number(value)) => value
            .as_f64()
            .ok_or_else(|| "ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(Value::String(value)) => value
            .trim()
            .parse::<f64>()
            .map_err(|_| "ANALYTICS_FLOAT_INVALID".to_owned())?,
        Some(_) => return Err("ANALYTICS_FLOAT_INVALID".to_owned()),
    };
    if number.is_finite() {
        Ok(number)
    } else {
        Err("ANALYTICS_FLOAT_NONFINITE".to_owned())
    }
}

fn identity_string(value: &Value) -> Option<String> {
    if !json_truthy(value) {
        return None;
    }
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Bool(value) => Some(if *value { "True" } else { "False" }.to_owned()),
        Value::Number(value) => Some(value.to_string()),
        Value::Null => None,
        Value::Array(_) | Value::Object(_) => serde_json::to_string(value).ok(),
    }
}

fn analytics_rows(path: &Path) -> Result<Vec<Value>, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("ANALYTICS_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("ANALYTICS_READ_FAILED:{error}")),
    };
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_ANALYTICS_BYTES {
        return Err("ANALYTICS_FILE_TOO_LARGE".to_owned());
    }
    let text = String::from_utf8(bytes).map_err(|_| "ANALYTICS_UTF8_INVALID".to_owned())?;
    let mut output = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value =
            serde_json::from_str(line).map_err(|_| "ANALYTICS_JSONL_INVALID".to_owned())?;
        if value.is_object() {
            output.push(value);
        }
    }
    Ok(output)
}

fn session_analytics(state_root: &Path) -> Result<Value, String> {
    let rows = analytics_rows(&state_root.join("analytics").join("events.jsonl"))?;
    let mut sessions = BTreeSet::new();
    let mut repositories = BTreeSet::new();
    let mut input_tokens = 0_i64;
    let mut cached_tokens = 0_i64;
    let mut output_tokens = 0_i64;
    let mut wall_time_ms = 0.0_f64;
    let mut cost_usd = 0.0_f64;
    let mut compaction_ms = 0.0_f64;
    let mut continuity = 0_u64;
    let mut route_denied = 0_u64;

    for row in &rows {
        let object = row
            .as_object()
            .ok_or_else(|| "ANALYTICS_ROW_INVALID".to_owned())?;
        if let Some(value) = object.get("session_id").and_then(identity_string) {
            sessions.insert(value);
        }
        if let Some(value) = object.get("repository_hash").and_then(identity_string) {
            repositories.insert(value);
        }
        input_tokens = input_tokens.saturating_add(python_int(object.get("input_tokens"))?.max(0));
        cached_tokens = cached_tokens
            .saturating_add(python_int(object.get("cached_input_tokens"))?.max(0));
        output_tokens =
            output_tokens.saturating_add(python_int(object.get("output_tokens"))?.max(0));
        wall_time_ms += python_float(object.get("wall_time_ms"))?.max(0.0);
        cost_usd += python_float(object.get("cost_usd"))?.max(0.0);
        compaction_ms += python_float(object.get("compaction_ms"))?.max(0.0);
        continuity += u64::from(object.get("continuity_restored").is_some_and(json_truthy));
        route_denied += u64::from(matches!(
            object.get("tool_route_allowed"),
            Some(Value::Bool(false))
        ));
    }

    Ok(json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "events": rows.len(),
        "sessions": sessions.len(),
        "repositories": repositories.len(),
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "billable_input_tokens": input_tokens.saturating_sub(cached_tokens).max(0),
            "output_tokens": output_tokens,
            "wall_time_ms": wall_time_ms,
            "cost_usd": cost_usd,
        },
        "continuity": {
            "restores": continuity,
            "compaction_wall_time_ms": compaction_ms,
        },
        "routing": {"denied": route_denied},
        "privacy": "content-free local aggregate",
    }))
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, String> {
    connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
            [table],
            |row| row.get::<_, bool>(0),
        )
        .map_err(|error| format!("STATS_SCHEMA_QUERY_FAILED:{error}"))
}

fn open_existing(path: &Path) -> Result<Option<Connection>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    Connection::open(path)
        .map(Some)
        .map_err(|error| format!("STATS_DATABASE_OPEN_FAILED:{error}"))
}

fn token_count(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_u64().and_then(|item| i64::try_from(item).ok()))
        .unwrap_or(0)
        .max(0)
}

fn token_attribution_summary(path: &Path) -> Result<Value, String> {
    let mut source_totals = TOKEN_SOURCES
        .iter()
        .map(|source| ((*source).to_owned(), 0_i64))
        .collect::<BTreeMap<_, _>>();
    let mut confidence_counts = CONFIDENCE_LEVELS
        .iter()
        .map(|level| ((*level).to_owned(), 0_i64))
        .collect::<BTreeMap<_, _>>();
    let mut receipts = 0_i64;
    let mut observed = 0_i64;
    let mut baseline = 0_i64;
    let mut baseline_rows = 0_i64;

    if let Some(connection) = open_existing(path)? {
        if table_exists(&connection, "token_attribution_receipts")? {
            let mut statement = connection
                .prepare(
                    "SELECT sources_json,confidence_json,baseline_tokens \
                     FROM token_attribution_receipts ORDER BY sequence",
                )
                .map_err(|error| format!("STATS_ATTRIBUTION_PREPARE_FAILED:{error}"))?;
            let rows = statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, Option<i64>>(2)?,
                    ))
                })
                .map_err(|error| format!("STATS_ATTRIBUTION_QUERY_FAILED:{error}"))?;
            for row in rows {
                let (sources_json, confidence_json, baseline_tokens) = row
                    .map_err(|error| format!("STATS_ATTRIBUTION_ROW_FAILED:{error}"))?;
                let sources: Value = serde_json::from_str(&sources_json)
                    .map_err(|_| "STATS_ATTRIBUTION_SOURCES_INVALID".to_owned())?;
                let confidence: Value = serde_json::from_str(&confidence_json)
                    .map_err(|_| "STATS_ATTRIBUTION_CONFIDENCE_INVALID".to_owned())?;
                receipts += 1;
                for source in TOKEN_SOURCES {
                    let count = sources.get(source).map_or(0, token_count);
                    observed = observed.saturating_add(count);
                    *source_totals.entry(source.to_owned()).or_default() += count;
                    if count > 0 {
                        let level = confidence
                            .get(source)
                            .and_then(Value::as_str)
                            .unwrap_or("UNKNOWN");
                        *confidence_counts.entry(level.to_owned()).or_default() += 1;
                    }
                }
                if let Some(value) = baseline_tokens {
                    baseline = baseline.saturating_add(value);
                    baseline_rows += 1;
                }
            }
        }
    }

    let complete_baseline = receipts > 0 && baseline_rows == receipts;
    Ok(json!({
        "schema_version": 1,
        "receipts": receipts,
        "session_id": Value::Null,
        "observed_tokens": observed,
        "baseline_tokens": if complete_baseline { Value::from(baseline) } else { Value::Null },
        "avoided_tokens": if complete_baseline { Value::from(baseline.saturating_sub(observed).max(0)) } else { Value::Null },
        "sources": source_totals,
        "confidence": confidence_counts,
        "claim_boundary": "Provider-observed totals and locally attributed sources remain distinct.",
    }))
}

fn usage_row(row: &Row<'_>) -> rusqlite::Result<UsageLedgerRow> {
    Ok(UsageLedgerRow {
        sequence: row.get(0)?,
        task_id: row.get(1)?,
        arm_id: row.get(2)?,
        repetition: row.get(3)?,
        cache_mode: row.get(4)?,
        provider: row.get(5)?,
        request_id_hash: row.get(6)?,
        provider_response_hash: row.get(7)?,
        fresh_input_tokens: row.get(8)?,
        cached_input_tokens: row.get(9)?,
        output_tokens: row.get(10)?,
        reasoning_tokens: row.get(11)?,
        quota_cost: row.get(12)?,
        hardware_hash: row.get(13)?,
        receipt_hash: row.get(14)?,
        previous_chain_hash: row.get(15)?,
        chain_hash: row.get(16)?,
        signature_mode: row.get(17)?,
        signature: row.get(18)?,
        raw_usage_hash: row.get(19)?,
        raw_usage_json: row.get(20)?,
        created_at: row.get(21)?,
    })
}

fn canonical_hash(value: &Value) -> Result<String, String> {
    serde_json::to_vec(value)
        .map(|bytes| sha256_hex(&bytes))
        .map_err(|_| "STATS_CANONICAL_JSON_FAILED".to_owned())
}

fn decode_hex(value: &str) -> Option<Vec<u8>> {
    if value.len() % 2 != 0 {
        return None;
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let high = (pair[0] as char).to_digit(16)?;
            let low = (pair[1] as char).to_digit(16)?;
            u8::try_from((high << 4) | low).ok()
        })
        .collect()
}

fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> Option<String> {
    let mut normalized = if key.len() > 64 {
        decode_hex(&sha256_hex(key))?
    } else {
        key.to_vec()
    };
    normalized.resize(64, 0);
    let mut inner = normalized.iter().map(|byte| byte ^ 0x36).collect::<Vec<_>>();
    inner.extend_from_slice(message);
    let inner_digest = decode_hex(&sha256_hex(&inner))?;
    let mut outer = normalized.iter().map(|byte| byte ^ 0x5c).collect::<Vec<_>>();
    outer.extend_from_slice(&inner_digest);
    Some(sha256_hex(&outer))
}

fn receipt_reasons(row: &UsageLedgerRow) -> Result<Vec<String>, String> {
    let mut reasons = Vec::new();
    if row.task_id.is_empty()
        || row.arm_id.is_empty()
        || row.repetition <= 0
        || row.cache_mode.is_empty()
    {
        reasons.push("receipt-identity-incomplete".to_owned());
    }
    if row.provider.is_empty()
        || row.request_id_hash.len() != 64
        || row.provider_response_hash.len() != 64
    {
        reasons.push("provider-evidence-incomplete".to_owned());
    }
    if [
        row.fresh_input_tokens,
        row.cached_input_tokens,
        row.output_tokens,
        row.reasoning_tokens,
    ]
    .into_iter()
    .any(|value| value < 0)
    {
        reasons.push("negative-token-count".to_owned());
    }
    if !row.quota_cost.is_finite() || row.quota_cost <= 0.0 {
        reasons.push("invalid-quota-cost".to_owned());
    }
    if row.hardware_hash.len() != 64 {
        reasons.push("hardware-hash-invalid".to_owned());
    }
    let payload = json!({
        "task_id": row.task_id,
        "arm_id": row.arm_id,
        "repetition": row.repetition,
        "cache_mode": row.cache_mode,
        "provider": row.provider,
        "request_id_hash": row.request_id_hash,
        "provider_response_hash": row.provider_response_hash,
        "fresh_input_tokens": row.fresh_input_tokens,
        "cached_input_tokens": row.cached_input_tokens,
        "output_tokens": row.output_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "quota_cost": row.quota_cost,
        "hardware_hash": row.hardware_hash,
    });
    if canonical_hash(&payload)? != row.receipt_hash {
        reasons.push("receipt-hash-mismatch".to_owned());
    }
    Ok(reasons)
}

fn provider_usage_integrity(path: &Path) -> Result<Value, String> {
    let mut rows = Vec::<UsageLedgerRow>::new();
    if let Some(connection) = open_existing(path)? {
        if table_exists(&connection, "usage_receipt_ledger")? {
            let mut statement = connection
                .prepare(
                    "SELECT sequence,task_id,arm_id,repetition,cache_mode,provider,request_id_hash,\
                     provider_response_hash,fresh_input_tokens,cached_input_tokens,output_tokens,\
                     reasoning_tokens,quota_cost,hardware_hash,receipt_hash,previous_chain_hash,\
                     chain_hash,signature_mode,signature,raw_usage_hash,raw_usage_json,created_at \
                     FROM usage_receipt_ledger ORDER BY sequence",
                )
                .map_err(|error| format!("STATS_USAGE_PREPARE_FAILED:{error}"))?;
            rows = statement
                .query_map([], usage_row)
                .map_err(|error| format!("STATS_USAGE_QUERY_FAILED:{error}"))?
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("STATS_USAGE_ROW_FAILED:{error}"))?;
        }
    }

    let mut reasons = Vec::<String>::new();
    let mut previous = ZERO_HASH.to_owned();
    let signing_key = env::var_os("SYNTAVRA_RECEIPT_SIGNING_KEY")
        .map(|value| value.to_string_lossy().into_owned().into_bytes());
    for (offset, row) in rows.iter().enumerate() {
        let expected_sequence = i64::try_from(offset + 1).unwrap_or(i64::MAX);
        if row.sequence != expected_sequence {
            reasons.push(format!("sequence-gap:{expected_sequence}->{}", row.sequence));
        }
        if row.previous_chain_hash != previous {
            reasons.push(format!("previous-chain-mismatch:{}", row.sequence));
        }
        for reason in receipt_reasons(row)? {
            reasons.push(format!("receipt:{}:{reason}", row.sequence));
        }
        let raw_hash = sha256_hex(row.raw_usage_json.as_bytes());
        if raw_hash != row.raw_usage_hash {
            reasons.push(format!("raw-usage-hash-mismatch:{}", row.sequence));
        }
        let envelope = json!({
            "schema_version": 1,
            "receipt_hash": row.receipt_hash,
            "previous_chain_hash": previous,
            "raw_usage_hash": row.raw_usage_hash,
            "created_at": row.created_at,
        });
        let calculated_chain = canonical_hash(&envelope)?;
        if calculated_chain != row.chain_hash {
            reasons.push(format!("chain-hash-mismatch:{}", row.sequence));
        }
        if row.signature_mode == "hmac-sha256" {
            match signing_key.as_deref() {
                None => reasons.push(format!("hmac-key-unavailable:{}", row.sequence)),
                Some(key) => {
                    let expected = hmac_sha256_hex(key, calculated_chain.as_bytes())
                        .ok_or_else(|| "STATS_HMAC_CALCULATION_FAILED".to_owned())?;
                    if expected != row.signature {
                        reasons.push(format!("signature-mismatch:{}", row.sequence));
                    }
                }
            }
        }
        previous = calculated_chain;
    }
    let hmac = !rows.is_empty()
        && rows
            .iter()
            .all(|row| row.signature_mode == "hmac-sha256");
    Ok(json!({
        "ok": reasons.is_empty(),
        "entries": rows.len(),
        "last_chain_hash": previous,
        "attestation": if hmac { "HMAC" } else { "HASH_CHAIN_ONLY" },
        "reasons": reasons,
    }))
}

fn read_install_receipt(path: &Path) -> Value {
    if !path.is_file() {
        return json!({});
    }
    match fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
    {
        Some(Value::Object(value)) => Value::Object(value),
        _ => json!({"invalid": true}),
    }
}

fn detected_project_hosts(project_root: &Path) -> Result<Vec<String>, String> {
    let command = vec!["host".to_owned(), "detect".to_owned()];
    let detected = super::native_host::execute(&command, &[], project_root)?;
    let mut hosts = detected
        .get("hosts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|row| {
            row.get("project_markers")
                .and_then(Value::as_array)
                .is_some_and(|markers| !markers.is_empty())
        })
        .filter_map(|row| row.get("host").and_then(Value::as_str).map(str::to_owned))
        .collect::<Vec<_>>();
    hosts.sort();
    hosts.dedup();
    Ok(hosts)
}

pub fn execute(project_root: &Path, state_root: &Path) -> Result<Value, String> {
    let receipt_path = state_root.join("install-receipt.json");
    let installed = receipt_path.is_file();
    let install_receipt = read_install_receipt(&receipt_path);
    let receipt_object = install_receipt.as_object().cloned().unwrap_or_default();
    let host_results = receipt_object
        .get("host_results")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let host_verification_passed = host_results
        .iter()
        .filter(|row| {
            row.get("verification")
                .and_then(|value| value.get("ok"))
                .is_some_and(json_truthy)
        })
        .count();
    let wall_time_ms = receipt_object
        .get("wall_time_ms")
        .cloned()
        .unwrap_or(Value::Null);
    let measured = !wall_time_ms.is_null();
    let has_receipt_payload = !receipt_object.is_empty();
    let usage_path = state_root.join("usage-receipts.sqlite3");
    let attribution = token_attribution_summary(&usage_path)?;
    let integrity = provider_usage_integrity(&usage_path)?;

    Ok(json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "installed": installed,
        "state_root": state_root,
        "detected_hosts": detected_project_hosts(project_root)?,
        "onboarding": {
            "measured": measured,
            "wall_time_ms": wall_time_ms,
            "host_installations": host_results.len(),
            "host_verification_passed": host_verification_passed,
            "claim": if has_receipt_payload { "LOCAL_INSTALL_AND_HOST_RECEIPT" } else { "ONBOARDING_NOT_MEASURED" },
        },
        "session_analytics": session_analytics(state_root)?,
        "savings_receipts": attribution.get("receipts").cloned().unwrap_or(Value::from(0)),
        "token_attribution": attribution,
        "provider_usage_integrity": integrity,
        "receipt_boundary": "provider-observed usage and source attribution remain distinct",
    }))
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn stats_command_is_supported() {
        assert!(supports(&["stats".to_owned()]));
    }
}
