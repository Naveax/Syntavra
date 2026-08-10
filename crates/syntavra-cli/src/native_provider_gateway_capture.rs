#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use base64::Engine as _;
use regex::Regex;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use syntavra_core::sha256_hex;
use unicode_normalization::UnicodeNormalization;

use super::super::native_evidence_store::NativeEvidenceStore;
use super::native_provider_gateway_prepare::{
    canonical_json, initialize_gateway, initialize_usage_ledger, now_seconds, option_value,
    output_value, project_root, stable_project_id,
};

const PROVIDER_DB: &str = "provider-gateway.sqlite3";
const RECEIPT_DB: &str = "usage-receipts.sqlite3";
const PREVIEW_MARKER: &str = "\n[… exact provider response stored as evidence …]";

#[derive(Debug, Clone)]
struct Plan {
    provider: String,
    model: String,
    request_hash: String,
    cache_key: String,
    request_handle: String,
    prompt_cache_mode: String,
    replay_cacheable: bool,
}

#[derive(Debug, Clone)]
struct NormalizedUsage {
    provider: String,
    fresh_input_tokens: i64,
    cached_input_tokens: i64,
    output_tokens: i64,
    reasoning_tokens: i64,
    source_fields: Vec<String>,
}

#[derive(Debug, Clone)]
struct SecurityScan {
    redacted_text: String,
    secret_types: Vec<String>,
    injection_risk: bool,
}

fn read_json_file(path: &str, label: &str) -> Result<Value, String> {
    let raw = fs::read_to_string(path).map_err(|error| format!("{label}_READ_FAILED:{error}"))?;
    serde_json::from_str(&raw).map_err(|error| format!("{label}_JSON_INVALID:{error}"))
}

fn required_string(map: &Map<String, Value>, key: &str) -> Result<String, String> {
    map.get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| format!("PROVIDER_CAPTURE_PLAN_{key}_INVALID"))
}

fn required_bool(map: &Map<String, Value>, key: &str) -> Result<bool, String> {
    map.get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("PROVIDER_CAPTURE_PLAN_{key}_INVALID"))
}

fn parse_plan(value: &Value) -> Result<Plan, String> {
    let map = value
        .as_object()
        .ok_or_else(|| "plan and response must be JSON objects".to_owned())?;
    for key in [
        "provider",
        "model",
        "request_hash",
        "cache_key",
        "request_handle",
        "stable_prefix_hash",
        "stable_message_count",
        "prompt_cache_mode",
        "replay_cacheable",
        "replay_hit",
        "replay_response_handle",
        "prepared_request",
        "reasons",
    ] {
        if !map.contains_key(key) {
            return Err(format!("PROVIDER_CAPTURE_PLAN_{key}_MISSING"));
        }
    }
    Ok(Plan {
        provider: required_string(map, "provider")?,
        model: required_string(map, "model")?,
        request_hash: required_string(map, "request_hash")?,
        cache_key: required_string(map, "cache_key")?,
        request_handle: required_string(map, "request_handle")?,
        prompt_cache_mode: required_string(map, "prompt_cache_mode")?,
        replay_cacheable: required_bool(map, "replay_cacheable")?,
    })
}

fn collect_text(value: &Value, output: &mut Vec<String>, depth: usize) {
    if depth > 8 {
        return;
    }
    match value {
        Value::String(text) => {
            if !text.trim().is_empty() {
                output.push(text.clone());
            }
        }
        Value::Object(map) => {
            let preferred = ["output_text", "text", "content"];
            for key in preferred {
                if let Some(Value::String(text)) = map.get(key) {
                    collect_text(&Value::String(text.clone()), output, depth + 1);
                }
            }
            for (key, child) in map {
                if preferred.contains(&key.as_str())
                    || matches!(key.as_str(), "usage" | "usageMetadata" | "metadata" | "id" | "model")
                {
                    continue;
                }
                collect_text(child, output, depth + 1);
            }
        }
        Value::Array(rows) => {
            for child in rows {
                collect_text(child, output, depth + 1);
            }
        }
        _ => {}
    }
}

fn normalize_text(text: &str) -> String {
    text.nfkc()
        .filter(|character| !matches!(character, '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{2060}' | '\u{feff}'))
        .collect::<String>()
        .replace("\r\n", "\n")
        .replace('\r', "\n")
}

fn luhn(value: &str) -> bool {
    let digits = value
        .chars()
        .filter_map(|character| character.to_digit(10))
        .collect::<Vec<_>>();
    if !(13..=19).contains(&digits.len()) || digits.iter().all(|digit| *digit == digits[0]) {
        return false;
    }
    let parity = digits.len() % 2;
    let total = digits
        .iter()
        .enumerate()
        .map(|(index, digit)| {
            let mut value = *digit;
            if index % 2 == parity {
                value *= 2;
                if value > 9 {
                    value -= 9;
                }
            }
            value
        })
        .sum::<u32>();
    total % 10 == 0
}

fn entropy(token: &str) -> f64 {
    if token.is_empty() {
        return 0.0;
    }
    let mut counts = BTreeMap::<char, usize>::new();
    for character in token.chars() {
        *counts.entry(character).or_default() += 1;
    }
    let length = token.chars().count() as f64;
    counts
        .values()
        .map(|count| {
            let probability = *count as f64 / length;
            -probability * probability.log2()
        })
        .sum()
}

fn unique_push(rows: &mut Vec<String>, value: impl Into<String>) {
    let value = value.into();
    if !rows.contains(&value) {
        rows.push(value);
    }
}

fn redact_regex(
    text: &str,
    pattern: &str,
    name: &str,
    generic: bool,
    private_key: bool,
    payment_card: bool,
) -> Result<(String, bool), String> {
    let regex = Regex::new(pattern).map_err(|error| format!("PROVIDER_SECURITY_REGEX_INVALID:{error}"))?;
    let mut found = false;
    let mut output = String::with_capacity(text.len());
    let mut cursor = 0usize;
    for captures in regex.captures_iter(text) {
        let Some(matched) = captures.get(0) else { continue };
        if payment_card && !luhn(matched.as_str()) {
            continue;
        }
        output.push_str(&text[cursor..matched.start()]);
        found = true;
        if generic {
            output.push_str(captures.get(1).map(|value| value.as_str()).unwrap_or("secret"));
            output.push_str("=<redacted:generic-assignment>");
        } else if private_key {
            output.push_str("-----BEGIN PRIVATE KEY-----<redacted:private-key>-----END PRIVATE KEY-----");
        } else {
            output.push_str(&format!("<redacted:{name}>"));
        }
        cursor = matched.end();
    }
    if !found {
        return Ok((text.to_owned(), false));
    }
    output.push_str(&text[cursor..]);
    Ok((output, true))
}

fn confusable_skeleton(text: &str) -> String {
    text.chars()
        .map(|character| match character {
            'а' => 'a',
            'е' => 'e',
            'о' => 'o',
            'р' => 'p',
            'с' => 'c',
            'у' => 'y',
            'х' => 'x',
            'Α' => 'A',
            'Β' => 'B',
            'Ε' => 'E',
            'Ζ' => 'Z',
            'Η' => 'H',
            'Ι' => 'I',
            'Κ' => 'K',
            'Μ' => 'M',
            'Ν' => 'N',
            'Ο' => 'O',
            'Ρ' => 'P',
            'Τ' => 'T',
            'Υ' => 'Y',
            'Χ' => 'X',
            value => value,
        })
        .collect()
}

fn percent_decode(token: &str) -> Option<Vec<u8>> {
    if token.len() % 3 != 0 {
        return None;
    }
    let bytes = token.as_bytes();
    let mut output = Vec::with_capacity(token.len() / 3);
    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] != b'%' {
            return None;
        }
        let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).ok()?;
        output.push(u8::from_str_radix(hex, 16).ok()?);
        index += 3;
    }
    Some(output)
}

fn decoded_candidates(text: &str) -> Vec<String> {
    let mut output = Vec::<String>::new();
    let mut seen = BTreeSet::<Vec<u8>>::new();
    let mut consumed = 0usize;

    let base64_regex = Regex::new(r"[A-Za-z0-9+/_-]{32,}={0,2}").expect("base64 regex");
    for matched in base64_regex.find_iter(text) {
        if output.len() >= 128 {
            break;
        }
        let token = matched.as_str();
        let padded = format!("{}{}", token, "=".repeat((4 - token.len() % 4) % 4));
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(&padded)
            .or_else(|_| base64::engine::general_purpose::URL_SAFE.decode(&padded));
        if let Ok(raw) = decoded {
            if raw.is_empty() || seen.contains(&raw) || consumed + raw.len() > 2 * 1024 * 1024 {
                continue;
            }
            consumed += raw.len();
            seen.insert(raw.clone());
            if let Ok(value) = String::from_utf8(raw) {
                output.push(value);
            }
        }
    }

    let hex_regex = Regex::new(r"[0-9a-fA-F]{32,}").expect("hex regex");
    for matched in hex_regex.find_iter(text) {
        if output.len() >= 128 {
            break;
        }
        let token = matched.as_str();
        if token.len() % 2 != 0 {
            continue;
        }
        let raw = (0..token.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&token[index..index + 2], 16))
            .collect::<Result<Vec<_>, _>>();
        if let Ok(raw) = raw {
            if raw.is_empty() || seen.contains(&raw) || consumed + raw.len() > 2 * 1024 * 1024 {
                continue;
            }
            consumed += raw.len();
            seen.insert(raw.clone());
            if let Ok(value) = String::from_utf8(raw) {
                output.push(value);
            }
        }
    }

    let percent_regex = Regex::new(r"(?:%[0-9A-Fa-f]{2}){8,}").expect("percent regex");
    for matched in percent_regex.find_iter(text) {
        if output.len() >= 128 {
            break;
        }
        if let Some(raw) = percent_decode(matched.as_str()) {
            if raw.is_empty() || seen.contains(&raw) || consumed + raw.len() > 2 * 1024 * 1024 {
                continue;
            }
            consumed += raw.len();
            seen.insert(raw.clone());
            if let Ok(value) = String::from_utf8(raw) {
                output.push(value);
            }
        }
    }
    output
}

fn scan_text_inner(text: &str, inspect_encoded: bool) -> Result<SecurityScan, String> {
    let normalized = normalize_text(text);
    let mut redacted = normalized.clone();
    let mut secret_types = Vec::<String>::new();

    let patterns = [
        (
            "generic-assignment",
            r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret|bearer|private[_-]?key|client[_-]?secret|session[_-]?id|cookie)\b\s*[:=]\s*([^\s,;]+)",
            true,
            false,
            false,
            false,
        ),
        ("aws-access-key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", false, false, false, false),
        ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b", false, false, false, false),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", false, false, false, false),
        ("database-uri", r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+", false, false, false, false),
        ("private-key", r"(?s)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----", false, true, false, false),
        ("payment-card", r"(?:\d[ -]*?){13,19}", false, false, true, true),
        ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", false, false, false, true),
    ];
    for (name, pattern, generic, private_key, payment_card, pii) in patterns {
        let (next, found) = redact_regex(&redacted, pattern, name, generic, private_key, payment_card)?;
        redacted = next;
        if found && !pii {
            unique_push(&mut secret_types, name);
        }
    }

    let injection_patterns = [
        r"(?is)(ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|do\s+not\s+follow\s+(?:the\s+)?(?:system|developer)|reveal\s+(?:the\s+)?(?:system\s+)?prompt|you\s+are\s+(?:chatgpt|an?\s+assistant)|</?(?:system|assistant|developer|tool)>|system\s+message\s*:|developer\s+message\s*:)",
        r"(?is)(önceki\s+(?:tüm\s+)?talimatları\s+(?:yoksay|unut)|sistem\s+istemini\s+(?:göster|açıkla)|geliştirici\s+mesajını\s+(?:göster|ifşa\s+et))",
        r"(?is)(ignora\s+(?:todas\s+)?las\s+instrucciones\s+anteriores|忽略(?:之前|所有).*指令|以前の指示を.*無視)",
    ];
    let skeleton = confusable_skeleton(&normalized);
    let mut injection_risk = false;
    for pattern in injection_patterns {
        let regex = Regex::new(pattern).map_err(|error| format!("PROVIDER_INJECTION_REGEX_INVALID:{error}"))?;
        if regex.is_match(&normalized) || (skeleton != normalized && regex.is_match(&skeleton)) {
            injection_risk = true;
        }
    }

    let entropy_regex = Regex::new(r"[A-Za-z0-9_\-+/=]{24,}").expect("entropy regex");
    for matched in entropy_regex.find_iter(&normalized) {
        let token = matched.as_str();
        let distinct = token.chars().collect::<BTreeSet<_>>().len();
        if entropy(token) >= 4.2
            && distinct >= 10
            && !token.to_lowercase().contains("http")
            && !token.to_lowercase().contains("sha256")
        {
            unique_push(&mut secret_types, "high-entropy-token");
        }
    }

    if inspect_encoded {
        for decoded in decoded_candidates(&normalized) {
            let nested = scan_text_inner(&decoded, false)?;
            for kind in nested.secret_types {
                unique_push(&mut secret_types, kind);
            }
            injection_risk |= nested.injection_risk;
        }
    }

    Ok(SecurityScan {
        redacted_text: redacted,
        secret_types,
        injection_risk,
    })
}

fn scan_text(text: &str) -> Result<SecurityScan, String> {
    scan_text_inner(text, true)
}

fn contains_tool_call(value: &Value) -> bool {
    match value {
        Value::Object(map) => map.iter().any(|(key, child)| {
            let normalized = key.to_lowercase();
            (matches!(normalized.as_str(), "tool_calls" | "tool_call" | "function_call" | "functioncall")
                && match child {
                    Value::Null => false,
                    Value::Bool(value) => *value,
                    Value::String(value) => !value.is_empty(),
                    Value::Array(value) => !value.is_empty(),
                    Value::Object(value) => !value.is_empty(),
                    Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
                })
                || contains_tool_call(child)
        }),
        Value::Array(rows) => rows.iter().any(contains_tool_call),
        _ => false,
    }
}

fn dig<'a>(value: &'a Map<String, Value>, path: &str) -> Option<&'a Value> {
    let mut current: &Value = &Value::Object(value.clone());
    let owned;
    // Avoid holding references into a temporary map clone by walking manually.
    let parts = path.split('.').collect::<Vec<_>>();
    let mut map = value;
    for (index, part) in parts.iter().enumerate() {
        let child = map.get(*part)?;
        if index + 1 == parts.len() {
            return Some(child);
        }
        map = child.as_object()?;
    }
    owned = current;
    let _ = owned;
    None
}

fn int_value(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(value)) => value.as_i64().or_else(|| value.as_u64().and_then(|v| i64::try_from(v).ok())).unwrap_or(0),
        Some(Value::String(value)) => value.parse::<i64>().unwrap_or(0),
        Some(Value::Bool(value)) => i64::from(*value),
        _ => 0,
    }
    .max(0)
}

fn take_usage(usage: &Map<String, Value>, paths: &[&str], fields: &mut Vec<String>) -> i64 {
    for path in paths {
        if let Some(value) = dig(usage, path) {
            unique_push(fields, *path);
            return int_value(Some(value));
        }
    }
    0
}

fn normalize_usage(provider: &str, response: &Value) -> Option<NormalizedUsage> {
    let response_map = response.as_object()?;
    let usage = response_map
        .get("usage")
        .and_then(Value::as_object)
        .unwrap_or(response_map);
    let mut fields = Vec::<String>::new();
    let cached = take_usage(
        usage,
        &[
            "input_tokens_details.cached_tokens",
            "prompt_tokens_details.cached_tokens",
            "cache_read_input_tokens",
            "cached_input_tokens",
            "cachedContentTokenCount",
        ],
        &mut fields,
    );
    let raw_input = take_usage(
        usage,
        &["input_tokens", "prompt_tokens", "promptTokenCount", "inputTokenCount"],
        &mut fields,
    );
    let output = take_usage(
        usage,
        &["output_tokens", "completion_tokens", "candidatesTokenCount", "outputTokenCount"],
        &mut fields,
    );
    let reasoning = take_usage(
        usage,
        &[
            "output_tokens_details.reasoning_tokens",
            "completion_tokens_details.reasoning_tokens",
            "reasoning_tokens",
            "thoughtsTokenCount",
        ],
        &mut fields,
    );
    let fresh = (raw_input - cached).max(0);
    if fields.is_empty() || fresh + cached + output + reasoning <= 0 {
        return None;
    }
    Some(NormalizedUsage {
        provider: provider.trim().to_lowercase(),
        fresh_input_tokens: fresh,
        cached_input_tokens: cached,
        output_tokens: output,
        reasoning_tokens: reasoning,
        source_fields: fields,
    })
}

fn usage_json(usage: Option<&NormalizedUsage>) -> Value {
    match usage {
        Some(value) => json!({
            "provider": value.provider,
            "fresh_input_tokens": value.fresh_input_tokens,
            "cached_input_tokens": value.cached_input_tokens,
            "output_tokens": value.output_tokens,
            "reasoning_tokens": value.reasoning_tokens,
            "source_fields": value.source_fields,
        }),
        None => json!({}),
    }
}

fn sha256_bytes(value: &[u8]) -> String {
    sha256_hex(value)
}

fn hmac_sha256(key: &[u8], message: &[u8]) -> String {
    let block_size = 64usize;
    let mut normalized = if key.len() > block_size {
        Sha256::digest(key).to_vec()
    } else {
        key.to_vec()
    };
    normalized.resize(block_size, 0);
    let mut inner_pad = vec![0x36; block_size];
    let mut outer_pad = vec![0x5c; block_size];
    for index in 0..block_size {
        inner_pad[index] ^= normalized[index];
        outer_pad[index] ^= normalized[index];
    }
    let mut inner = Sha256::new();
    inner.update(&inner_pad);
    inner.update(message);
    let inner_hash = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(&outer_pad);
    outer.update(inner_hash);
    format!("{:x}", outer.finalize())
}

fn valid_lower_sha(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn receipt_value(receipt: &Map<String, Value>, key: &str, default: &str) -> String {
    receipt
        .get(key)
        .filter(|value| match value {
            Value::Null => false,
            Value::String(value) => !value.is_empty(),
            Value::Bool(value) => *value,
            Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
            Value::Array(value) => !value.is_empty(),
            Value::Object(value) => !value.is_empty(),
        })
        .map(|value| match value {
            Value::String(value) => value.clone(),
            Value::Bool(value) => if *value { "True" } else { "False" }.to_owned(),
            Value::Number(value) => value.to_string(),
            other => serde_json::to_string(other).unwrap_or_else(|_| default.to_owned()),
        })
        .unwrap_or_else(|| default.to_owned())
}

fn record_usage_receipt(
    state_root: &Path,
    plan: &Plan,
    response: &Value,
    receipt: &Map<String, Value>,
) -> Result<i64, String> {
    let quota_cost = receipt
        .get("quota_cost")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let hardware_hash = receipt_value(receipt, "hardware_hash", "");
    if quota_cost <= 0.0 || !quota_cost.is_finite() || !valid_lower_sha(&hardware_hash) {
        return Ok(0);
    }
    let usage_payload = response
        .get("usage")
        .or_else(|| response.get("usageMetadata"))
        .unwrap_or(response);
    let normalized = normalize_usage(&plan.provider, usage_payload)
        .ok_or_else(|| "PROVIDER_RECEIPT_USAGE_INVALID".to_owned())?;
    let task_id = receipt_value(receipt, "task_id", "provider-task");
    let arm_id = receipt_value(receipt, "arm_id", "syntavra-provider-gateway");
    let repetition = receipt
        .get("repetition")
        .and_then(Value::as_i64)
        .unwrap_or(1)
        .max(1);
    let cache_mode = receipt_value(receipt, "cache_mode", &plan.prompt_cache_mode);
    let request_id = receipt_value(
        receipt,
        "request_id",
        response
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or(&plan.request_hash),
    );
    let request_id_hash = sha256_bytes(request_id.as_bytes());
    let provider_response_hash = sha256_bytes(&canonical_json(response)?);
    let raw_usage_json = String::from_utf8(canonical_json(usage_payload)?)
        .map_err(|error| format!("PROVIDER_RECEIPT_USAGE_UTF8_FAILED:{error}"))?;
    let raw_usage_hash = sha256_bytes(raw_usage_json.as_bytes());
    let receipt_payload = json!({
        "task_id": task_id,
        "arm_id": arm_id,
        "repetition": repetition,
        "cache_mode": cache_mode,
        "provider": normalized.provider,
        "request_id_hash": request_id_hash,
        "provider_response_hash": provider_response_hash,
        "fresh_input_tokens": normalized.fresh_input_tokens,
        "cached_input_tokens": normalized.cached_input_tokens,
        "output_tokens": normalized.output_tokens,
        "reasoning_tokens": normalized.reasoning_tokens,
        "quota_cost": quota_cost,
        "hardware_hash": hardware_hash,
    });
    let receipt_hash = sha256_bytes(&canonical_json(&receipt_payload)?);

    let mut connection = Connection::open(state_root.join(RECEIPT_DB))
        .map_err(|error| format!("PROVIDER_RECEIPT_DB_OPEN_FAILED:{error}"))?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("PROVIDER_RECEIPT_TRANSACTION_FAILED:{error}"))?;
    let previous_chain = transaction
        .query_row(
            "SELECT chain_hash FROM usage_receipt_ledger ORDER BY sequence DESC LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|error| format!("PROVIDER_RECEIPT_PREVIOUS_FAILED:{error}"))?
        .unwrap_or_else(|| "0".repeat(64));
    let created_at = now_seconds()?;
    let envelope = json!({
        "schema_version": 1,
        "receipt_hash": receipt_hash,
        "previous_chain_hash": previous_chain,
        "raw_usage_hash": raw_usage_hash,
        "created_at": created_at,
    });
    let chain_hash = sha256_bytes(&canonical_json(&envelope)?);
    let signing_key = env::var("SYNTAVRA_RECEIPT_SIGNING_KEY").ok();
    let (signature_mode, signature) = match signing_key {
        Some(key) if !key.is_empty() => (
            "hmac-sha256".to_owned(),
            hmac_sha256(key.as_bytes(), chain_hash.as_bytes()),
        ),
        _ => ("hash-chain-only".to_owned(), String::new()),
    };
    transaction
        .execute(
            "INSERT INTO usage_receipt_ledger(task_id,arm_id,repetition,cache_mode,provider,request_id_hash,provider_response_hash,fresh_input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,quota_cost,hardware_hash,receipt_hash,previous_chain_hash,chain_hash,signature_mode,signature,raw_usage_hash,raw_usage_json,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21)",
            (
                task_id,
                arm_id,
                repetition,
                cache_mode,
                normalized.provider,
                request_id_hash,
                provider_response_hash,
                normalized.fresh_input_tokens,
                normalized.cached_input_tokens,
                normalized.output_tokens,
                normalized.reasoning_tokens,
                quota_cost,
                hardware_hash,
                receipt_hash,
                previous_chain,
                chain_hash,
                signature_mode,
                signature,
                raw_usage_hash,
                raw_usage_json,
                created_at,
            ),
        )
        .map_err(|error| format!("PROVIDER_RECEIPT_INSERT_FAILED:{error}"))?;
    let sequence = transaction.last_insert_rowid();
    transaction
        .commit()
        .map_err(|error| format!("PROVIDER_RECEIPT_COMMIT_FAILED:{error}"))?;
    Ok(sequence)
}

fn replay_store(
    state_root: &Path,
    plan: &Plan,
    response_handle: &str,
    response_hash: &str,
    replay_ttl_seconds: i64,
) -> Result<(), String> {
    let mut connection = initialize_gateway(state_root)?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("PROVIDER_CAPTURE_CACHE_TRANSACTION_FAILED:{error}"))?;
    let now = now_seconds()?;
    transaction
        .execute(
            r#"
            INSERT INTO provider_response_cache(
                cache_key,provider,model,request_hash,request_handle,response_handle,response_hash,
                created_at,expires_at,hit_count,last_hit_at
            ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,0,0)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_handle=excluded.response_handle,
                response_hash=excluded.response_hash,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at
            "#,
            (
                plan.cache_key.as_str(),
                plan.provider.as_str(),
                plan.model.as_str(),
                plan.request_hash.as_str(),
                plan.request_handle.as_str(),
                response_handle,
                response_hash,
                now,
                now + replay_ttl_seconds.max(1) as f64,
            ),
        )
        .map_err(|error| format!("PROVIDER_CAPTURE_CACHE_WRITE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("PROVIDER_CAPTURE_CACHE_COMMIT_FAILED:{error}"))?;
    Ok(())
}

fn visible_preview(redacted: &str, preview_bytes: usize) -> String {
    let bytes = redacted.as_bytes();
    if bytes.len() <= preview_bytes {
        return redacted.to_owned();
    }
    let marker_bytes = PREVIEW_MARKER.as_bytes().len();
    let keep = preview_bytes.saturating_sub(marker_bytes);
    let mut end = keep.min(bytes.len());
    while end > 0 && std::str::from_utf8(&bytes[..end]).is_err() {
        end -= 1;
    }
    let prefix = std::str::from_utf8(&bytes[..end]).unwrap_or_default().trim_end();
    format!("{prefix}{PREVIEW_MARKER}")
}

pub(crate) fn capture(state_root: &Path) -> Result<Value, String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let project = project_root(&arguments)?;
    let project_id = stable_project_id(&project)?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    initialize_usage_ledger(state_root)?;
    let _gateway = initialize_gateway(state_root)?;

    let plan_path = option_value(&arguments, "--plan")?
        .ok_or_else(|| "PROVIDER_CAPTURE_PLAN_MISSING".to_owned())?;
    let response_path = option_value(&arguments, "--response")?
        .ok_or_else(|| "PROVIDER_CAPTURE_RESPONSE_MISSING".to_owned())?;
    let plan_value = read_json_file(&plan_path, "PROVIDER_CAPTURE_PLAN")?;
    let response = read_json_file(&response_path, "PROVIDER_CAPTURE_RESPONSE")?;
    if !response.is_object() {
        return Err("plan and response must be JSON objects".to_owned());
    }
    let plan = parse_plan(&plan_value)?;
    let raw = canonical_json(&response)?;
    let response_hash = sha256_bytes(&raw);
    let response_handle = evidence.put(
        &raw,
        "provider-response",
        &json!({
            "provider": plan.provider,
            "model": plan.model,
            "request_hash": plan.request_hash,
            "response_hash": response_hash,
        }),
    )?;

    let mut texts = Vec::<String>::new();
    collect_text(&response, &mut texts, 0);
    let mut deduped = Vec::<String>::new();
    for text in texts {
        if !deduped.contains(&text) {
            deduped.push(text);
        }
    }
    let security = scan_text(&deduped.join("\n"))?;
    let preview_limit = option_value(&arguments, "--preview-bytes")?
        .map(|value| value.parse::<usize>().map_err(|_| "PROVIDER_CAPTURE_PREVIEW_INVALID".to_owned()))
        .transpose()?
        .unwrap_or(4096);
    let visible = visible_preview(&security.redacted_text, preview_limit);

    let store_replay = !arguments.iter().any(|value| value == "--no-replay");
    let replay_ttl = option_value(&arguments, "--replay-ttl-seconds")?
        .map(|value| value.parse::<i64>().map_err(|_| "PROVIDER_CAPTURE_REPLAY_TTL_INVALID".to_owned()))
        .transpose()?
        .unwrap_or(900);
    let mut replay_stored = false;
    if store_replay && plan.replay_cacheable && !contains_tool_call(&response) {
        replay_store(state_root, &plan, &response_handle, &response_hash, replay_ttl)?;
        replay_stored = true;
    }

    let normalized_usage = normalize_usage(&plan.provider, &response);
    let receipt_sequence = match option_value(&arguments, "--receipt")? {
        Some(path) => {
            let value = read_json_file(&path, "PROVIDER_CAPTURE_RECEIPT")?;
            let map = value
                .as_object()
                .ok_or_else(|| "PROVIDER_CAPTURE_RECEIPT_NOT_OBJECT".to_owned())?;
            record_usage_receipt(state_root, &plan, &response, map)?
        }
        None => 0,
    };

    let result = json!({
        "provider": plan.provider,
        "model": plan.model,
        "request_hash": plan.request_hash,
        "response_hash": response_hash,
        "response_handle": response_handle,
        "visible_preview": visible,
        "original_bytes": raw.len(),
        "preview_bytes": visible.as_bytes().len(),
        "replay_stored": replay_stored,
        "receipt_sequence": receipt_sequence,
        "normalized_usage": usage_json(normalized_usage.as_ref()),
        "secret_types": security.secret_types,
        "injection_risk": security.injection_risk,
    });
    output_value(&result, &arguments)
}
