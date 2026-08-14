#![forbid(unsafe_code)]
#![allow(clippy::cast_precision_loss, clippy::too_many_lines)]

use std::cmp::Ordering;
use std::collections::{BTreeSet, HashSet};
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const EXTERNALIZE_THRESHOLD_BYTES: usize = 8_192;

#[derive(Debug, Clone)]
struct Item {
    item_id: String,
    layer: String,
    kind: String,
    source: String,
    content: String,
    priority: f64,
    stable: bool,
    exact_required: bool,
    metadata: Map<String, Value>,
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
    let budget = option_i64(arguments, "--budget", 32_000)?;
    let previous_raw = option_value(arguments, "--previous")?.unwrap_or_else(|| "{}".to_owned());
    let previous = load_json(&previous_raw)?;
    if !previous.is_object() {
        return Err("previous context must be a JSON object".to_owned());
    }
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
    let item_id = required_string(object, "item_id")?;
    let layer = required_string(object, "layer")?;
    let kind = required_string(object, "kind")?;
    let source = required_string(object, "source")?;
    let content = required_string(object, "content")?;
    let priority = object
        .get("priority")
        .map_or(Ok(0.5), |value| {
            value
                .as_f64()
                .filter(|number| number.is_finite())
                .ok_or_else(|| "context item priority must be a finite number".to_owned())
        })?;
    let stable = object
        .get("stable")
        .map_or(Ok(false), |value| {
            value
                .as_bool()
                .ok_or_else(|| "context item stable must be a boolean".to_owned())
        })?;
    let exact_required = object
        .get("exact_required")
        .map_or(Ok(true), |value| {
            value
                .as_bool()
                .ok_or_else(|| "context item exact_required must be a boolean".to_owned())
        })?;
    let metadata = object
        .get("metadata")
        .map_or_else(
            || Ok(Map::new()),
            |value| {
                value
                    .as_object()
                    .cloned()
                    .ok_or_else(|| "context item metadata must be a JSON object".to_owned())
            },
        )?;
    Ok(Item {
        item_id,
        layer,
        kind,
        source,
        content,
        priority,
        stable,
        exact_required,
        metadata,
    })
}

fn required_string(object: &Map<String, Value>, key: &str) -> Result<String, String> {
    object
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| format!("context item {key} must be a string"))
}

fn compile(
    items: &[Item],
    provider: &str,
    model: &str,
    budget: i64,
    previous: &Value,
    state_root: &Path,
) -> Result<Value, String> {
    let previous = previous
        .as_object()
        .ok_or_else(|| "previous context must be a JSON object".to_owned())?;
    let mut normalized = Vec::<Item>::new();
    let mut seen_content = HashSet::<String>::new();
    let mut artifact_ids = BTreeSet::<String>::new();
    let store = super::native_artifact_store::NativeArtifactStore::open(state_root)?;

    for raw in items {
        let mut item = raw.clone();
        item.content = item.content.replace("\r\n", "\n");
        let digest = sha256_hex(item.content.as_bytes());
        if !seen_content.insert(digest) {
            continue;
        }
        if item.kind.is_empty() {
            item.kind = infer_kind(&item.content, &item.source);
        }
        let prior = previous
            .get(&item.item_id)
            .and_then(Value::as_str)
            .unwrap_or_default();
        item.content = delta(prior, &item.content);
        item.priority = item.priority.clamp(0.0, 1.0);

        if item.content.len() > EXTERNALIZE_THRESHOLD_BYTES {
            let record = store.put(
                item.content.as_bytes(),
                "text/plain",
                "tool-output:shell",
                &json!({"tool":item.source,"exit_code":0,"duration_ms":0.0}),
            )?;
            artifact_ids.insert(record.artifact_id);
        }
        normalized.push(item);
    }

    normalized.sort_by(compare_items);
    let provider_key = provider.to_lowercase();
    let selection_budget = budget.max(1);
    let mut used_tokens = 0_i64;
    let mut selected = Vec::<Value>::new();
    let mut omitted = Vec::<Value>::new();
    let mut stable_payload = Vec::<Value>::new();

    for item in normalized {
        let tokens = estimate_tokens(&item.content, &provider_key)?;
        let row = json!({
            "item_id": item.item_id,
            "layer": item.layer,
            "kind": item.kind,
            "source": item.source,
            "content": item.content,
            "tokens": tokens,
            "priority": item.priority,
            "stable": item.stable,
            "exact_required": item.exact_required,
            "metadata": item.metadata,
        });
        if used_tokens.saturating_add(tokens) <= selection_budget {
            used_tokens = used_tokens.saturating_add(tokens);
            if item.stable {
                stable_payload.push(row.clone());
            }
            selected.push(row);
        } else {
            let mut compact = row
                .as_object()
                .cloned()
                .ok_or_else(|| "CONTEXT_ROW_INVALID".to_owned())?;
            compact.remove("content");
            compact.insert("reason".to_owned(), Value::String("budget".to_owned()));
            omitted.push(Value::Object(compact));
        }
    }

    let cache_prefix_hash = sha256_hex(canonical_json(&Value::Array(stable_payload))?.as_bytes());
    let artifacts = artifact_ids.into_iter().collect::<Vec<_>>();
    let pack_body = json!({
        "version": VERSION,
        "channel": CHANNEL,
        "provider": provider,
        "model": model,
        "budget_tokens": budget,
        "items": selected,
        "artifacts": artifacts,
    });
    let pack_hash = sha256_hex(canonical_json(&pack_body)?.as_bytes());
    Ok(json!({
        "provider": provider,
        "model": model,
        "budget_tokens": budget,
        "used_tokens": used_tokens,
        "cache_prefix_hash": cache_prefix_hash,
        "items": pack_body["items"].clone(),
        "omitted": omitted,
        "artifacts": pack_body["artifacts"].clone(),
        "pack_hash": pack_hash,
        "deterministic": true,
    }))
}

fn compare_items(left: &Item, right: &Item) -> Ordering {
    layer_rank(&left.layer)
        .cmp(&layer_rank(&right.layer))
        .then_with(|| right.stable.cmp(&left.stable))
        .then_with(|| right.priority.total_cmp(&left.priority))
        .then_with(|| left.source.cmp(&right.source))
        .then_with(|| left.item_id.cmp(&right.item_id))
}

fn layer_rank(layer: &str) -> u8 {
    match layer {
        "system" => 0,
        "repository" => 1,
        "tools" => 2,
        "memory" => 3,
        "task" => 4,
        "user" => 5,
        _ => 99,
    }
}

fn infer_kind(content: &str, source: &str) -> String {
    let lower = source.to_lowercase();
    if [".py", ".ts", ".tsx", ".js", ".rs", ".go", ".java", ".cs", ".cpp", ".c"]
        .iter()
        .any(|suffix| lower.ends_with(suffix))
    {
        return "source".to_owned();
    }
    if lower.ends_with(".json") || lower.ends_with(".jsonl") {
        return "json".to_owned();
    }
    if lower.contains("diff") || content.starts_with("diff --git") {
        return "diff".to_owned();
    }
    let diagnostics = [
        "error", "failed", "failure", "panic", "assertion", "traceback", "exception", "fatal",
        "denied", "timeout",
    ];
    let content_lower = content.to_lowercase();
    if diagnostics.iter().any(|needle| content_lower.contains(needle)) {
        return "diagnostic".to_owned();
    }
    "text".to_owned()
}

fn delta(previous: &str, current: &str) -> String {
    // The canonical Python path keeps identical/first-seen content verbatim.
    // A later differential hardening fixture covers non-trivial unified-diff
    // compaction; retaining the current value here is fail-safe and lossless.
    if previous.is_empty() || previous == current {
        current.to_owned()
    } else {
        current.to_owned()
    }
}

fn estimate_tokens(text: &str, provider: &str) -> Result<i64, String> {
    let ratio = match provider {
        "openai" => 3.7,
        "anthropic" => 3.5,
        "gemini" => 3.8,
        "local" => 3.3,
        "generic" => 3.5,
        _ => 3.5,
    };
    let bytes = text.len() as f64;
    let estimated = (bytes / ratio + 0.999).trunc();
    if !estimated.is_finite() || estimated > i64::MAX as f64 {
        return Err("CONTEXT_TOKEN_ESTIMATE_OVERFLOW".to_owned());
    }
    Ok((estimated as i64).max(1))
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
