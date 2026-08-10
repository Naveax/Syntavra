#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

#[derive(Debug, Clone)]
struct Item {
    role: String,
    content: String,
    priority: i64,
    stable: bool,
    source: String,
    content_hash: String,
    bytes: usize,
    estimated_tokens: i64,
}

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2 && command[0] == "run" && command[1] == "context-compile"
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    let source = positional_after(
        arguments,
        "context-compile",
        0,
        &["--provider", "--model", "--budget", "--previous"],
    )?;
    let provider = option_value(arguments, "--provider")?.unwrap_or_else(|| "generic".to_owned());
    let model = option_value(arguments, "--model")?.unwrap_or_else(|| "unknown".to_owned());
    let budget = option_i64(arguments, "--budget", 32_000)?.max(1);
    let previous_raw = option_value(arguments, "--previous")?.unwrap_or_else(|| "{}".to_owned());
    let previous = load_json(&previous_raw)?;
    let rows = load_json(source)?;
    let rows = rows
        .as_array()
        .ok_or_else(|| "context items must be a JSON list".to_owned())?;
    let mut items = Vec::with_capacity(rows.len());
    for row in rows {
        items.push(parse_item(row)?);
    }
    compile(&items, &provider, &model, budget, &previous, state_root).map(Some)
}

fn parse_item(row: &Value) -> Result<Item, String> {
    let object = row
        .as_object()
        .ok_or_else(|| "context item must be a JSON object".to_owned())?;
    let role = object
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or("user")
        .to_owned();
    let content = object
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| "context item content must be a string".to_owned())?
        .to_owned();
    let priority = object.get("priority").and_then(Value::as_i64).unwrap_or(0);
    let stable = object
        .get("stable")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let source = object
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or("runtime")
        .to_owned();
    let bytes = content.as_bytes().len();
    let estimated_tokens = i64::try_from((bytes + 3) / 4)
        .map_err(|_| "CONTEXT_TOKEN_ESTIMATE_OVERFLOW".to_owned())?
        .max(1);
    let content_hash = sha256_hex(content.as_bytes());
    Ok(Item {
        role,
        content,
        priority,
        stable,
        source,
        content_hash,
        bytes,
        estimated_tokens,
    })
}

fn compile(
    items: &[Item],
    provider: &str,
    model: &str,
    budget: i64,
    previous: &Value,
    state_root: &Path,
) -> Result<Value, String> {
    let provider_name = provider.trim().to_lowercase();
    let raw_tokens = items.iter().map(|item| item.estimated_tokens).sum::<i64>();
    let previous_hashes = previous
        .get("ordered_hashes")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let mut stable = Vec::<(usize, &Item)>::new();
    let mut volatile = Vec::<(usize, &Item)>::new();
    for (index, item) in items.iter().enumerate() {
        let provider_stable = matches!(item.role.as_str(), "system" | "developer")
            || (item.role == "tool" && item.stable);
        if item.stable || provider_stable {
            stable.push((index, item));
        } else {
            volatile.push((index, item));
        }
    }
    stable.sort_by(|left, right| {
        right
            .1
            .priority
            .cmp(&left.1.priority)
            .then_with(|| left.0.cmp(&right.0))
    });
    volatile.sort_by(|left, right| {
        right
            .1
            .priority
            .cmp(&left.1.priority)
            .then_with(|| left.0.cmp(&right.0))
    });

    let mut ordered = stable;
    ordered.extend(volatile);
    let mut selected = Vec::<Value>::new();
    let mut selected_hashes = Vec::<String>::new();
    let mut externalized = Vec::<Value>::new();
    let mut used = 0i64;
    let store = super::native_artifact_store::NativeArtifactStore::open(state_root)?;

    for (original_index, item) in ordered {
        if used + item.estimated_tokens <= budget {
            selected.push(json!({
                "role": item.role,
                "content": item.content,
                "priority": item.priority,
                "stable": item.stable,
                "source": item.source,
                "content_hash": item.content_hash,
                "estimated_tokens": item.estimated_tokens,
                "bytes": item.bytes,
                "original_index": original_index,
            }));
            selected_hashes.push(item.content_hash.clone());
            used += item.estimated_tokens;
            continue;
        }
        let record = store.put(
            item.content.as_bytes(),
            "text/plain",
            "context-externalization",
            &json!({
                "role":item.role,
                "source":item.source,
                "priority":item.priority,
                "content_hash":item.content_hash,
                "provider":provider_name,
                "model":model,
            }),
        )?;
        externalized.push(json!({
            "role":item.role,
            "source":item.source,
            "priority":item.priority,
            "content_hash":item.content_hash,
            "estimated_tokens":item.estimated_tokens,
            "bytes":item.bytes,
            "artifact_id":record.artifact_id,
            "reason":"context-budget-exceeded",
            "original_index":original_index,
        }));
    }

    // Restore source order inside the selected stable/volatile buckets only after
    // the priority decision has been made. Stable content remains at the front so
    // provider prompt caches see the longest deterministic prefix possible.
    selected.sort_by(|left, right| {
        let left_stable = matches!(left["role"].as_str(), Some("system" | "developer"))
            || left["stable"].as_bool().unwrap_or(false);
        let right_stable = matches!(right["role"].as_str(), Some("system" | "developer"))
            || right["stable"].as_bool().unwrap_or(false);
        right_stable.cmp(&left_stable).then_with(|| {
            left["original_index"]
                .as_i64()
                .cmp(&right["original_index"].as_i64())
        })
    });
    selected_hashes = selected
        .iter()
        .filter_map(|row| row["content_hash"].as_str())
        .map(str::to_owned)
        .collect();

    let stable_count = selected
        .iter()
        .take_while(|row| {
            matches!(row["role"].as_str(), Some("system" | "developer"))
                || row["stable"].as_bool().unwrap_or(false)
        })
        .count();
    let stable_hashes = selected_hashes
        .iter()
        .take(stable_count)
        .cloned()
        .collect::<Vec<_>>();
    let stable_prefix_hash = sha256_hex(
        canonical_json(
            &serde_json::to_value(&stable_hashes)
                .map_err(|error| format!("CONTEXT_STABLE_HASH_VALUE_FAILED:{error}"))?,
        )?
        .as_bytes(),
    );
    let unchanged_prefix = previous_hashes
        .iter()
        .zip(selected_hashes.iter())
        .take_while(|(left, right)| left == right)
        .count();
    let selected_tokens = selected
        .iter()
        .map(|row| row["estimated_tokens"].as_i64().unwrap_or(0))
        .sum::<i64>();
    let externalized_tokens = externalized
        .iter()
        .map(|row| row["estimated_tokens"].as_i64().unwrap_or(0))
        .sum::<i64>();
    let cache_instruction = cache_instruction(&provider_name, stable_count, &stable_prefix_hash);
    let mut result = json!({
        "ok": true,
        "version": "0.0.1",
        "channel": "pre-release",
        "provider": provider_name,
        "model": model,
        "budget_tokens": budget,
        "raw_tokens": raw_tokens,
        "selected_tokens": selected_tokens,
        "externalized_tokens": externalized_tokens,
        "saved_context_tokens": (raw_tokens - selected_tokens).max(0),
        "selected": selected,
        "externalized": externalized,
        "ordered_hashes": selected_hashes,
        "stable_prefix_count": stable_count,
        "stable_prefix_hash": stable_prefix_hash,
        "unchanged_prefix_count": unchanged_prefix,
        "cache_instruction": cache_instruction,
        "exact_recovery": true,
        "claim_boundary": "local context compilation only; provider-observed net savings require paired provider receipts",
    });
    let receipt_material = result.clone();
    result["compile_receipt"] = Value::String(format!(
        "sha256:{}",
        sha256_hex(canonical_json(&receipt_material)?.as_bytes())
    ));
    Ok(result)
}

fn cache_instruction(provider: &str, stable_count: usize, prefix_hash: &str) -> Value {
    match provider {
        "anthropic" => json!({
            "provider":"anthropic",
            "strategy":"stable-prefix-cache-control",
            "stable_messages":stable_count,
            "stable_prefix_hash":prefix_hash,
            "cache_control":{"type":"ephemeral"},
        }),
        "gemini" | "google" => json!({
            "provider":provider,
            "strategy":"cached-content-prefix",
            "stable_messages":stable_count,
            "stable_prefix_hash":prefix_hash,
        }),
        "openai" | "openai-compatible" => json!({
            "provider":provider,
            "strategy":"stable-prefix",
            "stable_messages":stable_count,
            "stable_prefix_hash":prefix_hash,
        }),
        _ => json!({
            "provider":provider,
            "strategy":"generic-stable-prefix",
            "stable_messages":stable_count,
            "stable_prefix_hash":prefix_hash,
        }),
    }
}

fn load_json(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value).map_err(|error| format!("CONTEXT_SOURCE_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("CONTEXT_JSON_INVALID:{error}"))
}

fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(value))
        .map_err(|error| format!("CONTEXT_JSON_SERIALIZE_FAILED:{error}"))
}

fn sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sort_json(&map[key]));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(sort_json).collect()),
        _ => value.clone(),
    }
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut output = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let current = &arguments[index];
        let found = if current == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            current
                .strip_prefix(flag)
                .and_then(|tail| tail.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(found) = found {
            output = Some(found);
        }
        index += 1;
    }
    Ok(output)
}

fn option_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    position: usize,
    value_flags: &[&str],
) -> Result<&'a str, String> {
    let mut index = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("CONTEXT_ACTION_NOT_FOUND:{action}"))?;
    let mut values = Vec::new();
    while index < arguments.len() {
        if value_flags.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        values.push(arguments[index].as_str());
        index += 1;
    }
    values
        .get(position)
        .copied()
        .ok_or_else(|| format!("CONTEXT_POSITIONAL_MISSING:{action}:{position}"))
}
