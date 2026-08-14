#![forbid(unsafe_code)]

use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

const VOLATILE_KEYS: [&str; 7] = [
    "timestamp",
    "request_id",
    "trace_id",
    "nonce",
    "usage",
    "cost",
    "latency_ms",
];

#[derive(Debug, Clone, PartialEq, Eq)]
struct Inputs {
    source: String,
    provider: String,
    model: String,
    ttl_seconds: Option<i64>,
    reorder: bool,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "cache-plan")
}

fn parse(arguments: &[String]) -> Result<Inputs, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "cache-plan")
        .map(|index| index + 2)
        .ok_or_else(|| "CACHE_PLAN_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut source = None;
    let mut provider = None;
    let mut model = None;
    let mut ttl_seconds = None;
    let mut reorder = true;
    let mut positional_only = false;
    let mut index = 0_usize;

    while index < tail.len() {
        let value = &tail[index];
        if !positional_only && value == "--" {
            positional_only = true;
            index += 1;
            continue;
        }
        if !positional_only && value == "--no-reorder" {
            reorder = false;
            index += 1;
            continue;
        }
        if !positional_only && value == "--provider" {
            let next = tail
                .get(index + 1)
                .ok_or_else(|| "CACHE_PLAN_PROVIDER_VALUE_MISSING".to_owned())?;
            provider = Some(next.clone());
            index += 2;
            continue;
        }
        if !positional_only && value == "--model" {
            let next = tail
                .get(index + 1)
                .ok_or_else(|| "CACHE_PLAN_MODEL_VALUE_MISSING".to_owned())?;
            model = Some(next.clone());
            index += 2;
            continue;
        }
        if !positional_only && value == "--ttl" {
            let next = tail
                .get(index + 1)
                .ok_or_else(|| "CACHE_PLAN_TTL_VALUE_MISSING".to_owned())?;
            ttl_seconds = Some(
                next.parse::<i64>()
                    .map_err(|_| "CACHE_PLAN_TTL_INVALID".to_owned())?,
            );
            index += 2;
            continue;
        }
        if !positional_only {
            if let Some(next) = value.strip_prefix("--provider=") {
                provider = Some(next.to_owned());
                index += 1;
                continue;
            }
            if let Some(next) = value.strip_prefix("--model=") {
                model = Some(next.to_owned());
                index += 1;
                continue;
            }
            if let Some(next) = value.strip_prefix("--ttl=") {
                ttl_seconds = Some(
                    next.parse::<i64>()
                        .map_err(|_| "CACHE_PLAN_TTL_INVALID".to_owned())?,
                );
                index += 1;
                continue;
            }
            if value.starts_with('-') {
                return Err(format!("CACHE_PLAN_OPTION_UNKNOWN:{value}"));
            }
        }
        if source.replace(value.clone()).is_some() {
            return Err(format!("CACHE_PLAN_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }

    Ok(Inputs {
        source: source.ok_or_else(|| "CACHE_PLAN_SOURCE_MISSING".to_owned())?,
        provider: provider.ok_or_else(|| "CACHE_PLAN_PROVIDER_MISSING".to_owned())?,
        model: model.ok_or_else(|| "CACHE_PLAN_MODEL_MISSING".to_owned())?,
        ttl_seconds,
        reorder,
    })
}

fn load_messages(source: &str) -> Result<Vec<Value>, String> {
    let path = Path::new(source);
    let raw = if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("CACHE_PLAN_SOURCE_READ_FAILED:{error}"))?
    } else {
        source.to_owned()
    };
    let value = serde_json::from_str::<Value>(&raw)
        .map_err(|error| format!("CACHE_PLAN_SOURCE_JSON_INVALID:{error}"))?;
    let rows = value
        .as_array()
        .ok_or_else(|| "cache-plan source must contain a JSON message list".to_owned())?;
    for row in rows {
        if !row.is_object() {
            return Err("CACHE_PLAN_MESSAGE_NOT_OBJECT".to_owned());
        }
    }
    Ok(rows.clone())
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => {
            if let Some(value) = value.as_i64() {
                value != 0
            } else if let Some(value) = value.as_u64() {
                value != 0
            } else {
                value.as_f64().is_some_and(|value| value != 0.0)
            }
        }
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn stable_message(message: &Map<String, Value>) -> bool {
    let role = message
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    if matches!(role.as_str(), "system" | "developer") {
        return true;
    }
    if role == "tool" && message.get("cache_control").and_then(Value::as_str) == Some("stable") {
        return true;
    }
    truthy(message.get("stable")) || truthy(message.get("cacheable"))
}

fn clean(value: &Value) -> Value {
    match value {
        Value::Object(values) => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                if VOLATILE_KEYS.contains(&key.as_str()) || key.starts_with('_') {
                    continue;
                }
                output.insert(key.clone(), clean(&values[key]));
            }
            Value::Object(output)
        }
        Value::Array(values) => Value::Array(values.iter().map(clean).collect()),
        _ => value.clone(),
    }
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&clean(value)).map_err(|error| format!("CACHE_PLAN_JSON_SERIALIZE_FAILED:{error}"))
}

fn sha256(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

fn role_string(message: &Map<String, Value>) -> String {
    let Some(value) = message.get("role") else {
        return "unknown".to_owned();
    };
    if !truthy(Some(value)) {
        return "unknown".to_owned();
    }
    match value {
        Value::String(value) => value.clone(),
        Value::Bool(value) => {
            if *value {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        Value::Null => "unknown".to_owned(),
        _ => value.to_string(),
    }
}

fn provider_ttl(provider: &str) -> i64 {
    match provider {
        "anthropic" => 300,
        "google" | "gemini" => 3600,
        "openai" | "groq" | "openrouter" => 600,
        _ => 600,
    }
}

fn unix_now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|error| format!("CACHE_PLAN_CLOCK_FAILED:{error}"))
}

fn build_plan(messages: &[Value], inputs: &Inputs, now: f64) -> Result<Value, String> {
    let provider = {
        let value = inputs.provider.trim().to_lowercase();
        if value.is_empty() {
            "unknown".to_owned()
        } else {
            value
        }
    };
    let ttl = match inputs.ttl_seconds {
        Some(0) | None => provider_ttl(&provider),
        Some(value) => value,
    };

    let mut stable_rows = Vec::new();
    let mut volatile_rows = Vec::new();
    for row in messages {
        let object = row
            .as_object()
            .ok_or_else(|| "CACHE_PLAN_MESSAGE_NOT_OBJECT".to_owned())?;
        if stable_message(object) {
            stable_rows.push(row.clone());
        } else {
            volatile_rows.push(row.clone());
        }
    }
    let stable_count = stable_rows.len();
    let volatile_count = volatile_rows.len();
    let ordered = if inputs.reorder {
        stable_rows
            .iter()
            .chain(volatile_rows.iter())
            .cloned()
            .collect::<Vec<_>>()
    } else {
        messages.to_vec()
    };

    let stable_prefix = Value::Array(
        ordered
            .iter()
            .take(stable_count)
            .map(clean)
            .collect::<Vec<_>>(),
    );
    let stable_hash = sha256(&canonical_json(&stable_prefix)?);

    let mut segments = Vec::new();
    let mut cacheable_tokens = 0_u64;
    let mut volatile_tokens = 0_u64;
    for row in &ordered {
        let object = row
            .as_object()
            .ok_or_else(|| "CACHE_PLAN_MESSAGE_NOT_OBJECT".to_owned())?;
        let cleaned = clean(row);
        let raw = canonical_json(&cleaned)?;
        let stable = stable_message(object);
        let tokens = std::cmp::max(1_usize, raw.len() / 4);
        let tokens = u64::try_from(tokens).map_err(|_| "CACHE_PLAN_TOKEN_COUNT_INVALID".to_owned())?;
        if stable {
            cacheable_tokens = cacheable_tokens.saturating_add(tokens);
        } else {
            volatile_tokens = volatile_tokens.saturating_add(tokens);
        }
        segments.push(json!({
            "role": role_string(object),
            "stable": stable,
            "bytes": raw.len(),
            "tokens_estimate": tokens,
            "content_hash": sha256(&raw),
            "reason": if stable { "stable-prefix" } else { "volatile-tail" },
        }));
    }

    let reordered = inputs.reorder && ordered != messages;
    Ok(json!({
        "provider": provider,
        "model": inputs.model,
        "stable_prefix_hash": stable_hash,
        "stable_messages": stable_count,
        "volatile_messages": volatile_count,
        "cacheable_tokens": cacheable_tokens,
        "volatile_tokens": volatile_tokens,
        "ttl_seconds": ttl,
        "expires_at": now + ttl as f64,
        "refresh_after": now + ttl as f64 * 0.75,
        "reordered": reordered,
        "segments": segments,
    }))
}

fn write_atomic(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "CACHE_PLAN_STATE_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("CACHE_PLAN_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    let mut random = [0_u8; 6];
    OsRng.fill_bytes(&mut random);
    let suffix = random
        .iter()
        .map(|value| format!("{value:02x}"))
        .collect::<String>();
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "CACHE_PLAN_STATE_NAME_INVALID".to_owned())?;
    let temporary = parent.join(format!(".{name}.{suffix}.tmp"));
    let mut bytes = serde_json::to_vec(&clean(value))
        .map_err(|error| format!("CACHE_PLAN_STATE_SERIALIZE_FAILED:{error}"))?;
    bytes.push(b'\n');
    let result = (|| -> std::io::Result<()> {
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        output.write_all(&bytes)?;
        output.flush()?;
        output.sync_all()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))?;
        }
        #[cfg(windows)]
        if path.exists() {
            fs::remove_file(path)?;
        }
        fs::rename(&temporary, path)
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("CACHE_PLAN_STATE_WRITE_FAILED:{error}"));
    }
    Ok(())
}

fn persist(state_root: &Path, plan: &Value) -> Result<(), String> {
    let path = state_root.join("cache").join("plans.json");
    let current = if path.is_file() {
        let raw = fs::read_to_string(&path)
            .map_err(|error| format!("CACHE_PLAN_STATE_READ_FAILED:{error}"))?;
        serde_json::from_str::<Value>(&raw)
            .map_err(|error| format!("CACHE_PLAN_STATE_JSON_INVALID:{error}"))?
    } else {
        json!({})
    };
    let mut plans = current
        .get("plans")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let provider = plan["provider"].as_str().unwrap_or("unknown");
    let model = plan["model"].as_str().unwrap_or_default();
    let stable_hash = plan["stable_prefix_hash"].as_str().unwrap_or_default();
    plans.insert(format!("{provider}:{model}:{stable_hash}"), plan.clone());
    write_atomic(
        &path,
        &json!({
            "plans": plans,
            "updated_at": unix_now()?,
        }),
    )
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let inputs = parse(arguments)?;
    let messages = load_messages(&inputs.source)?;
    let plan = build_plan(&messages, &inputs, unix_now()?)?;
    persist(state_root, &plan)?;
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::{build_plan, parse, Inputs};
    use serde_json::json;

    #[test]
    fn parses_required_options_and_last_value_wins() {
        let arguments = [
            "syntavra",
            "run",
            "cache-plan",
            "[]",
            "--provider",
            "openai",
            "--provider=anthropic",
            "--model",
            "first",
            "--model=second",
            "--ttl",
            "0",
            "--no-reorder",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        let value = parse(&arguments).expect("parse");
        assert_eq!(value.provider, "anthropic");
        assert_eq!(value.model, "second");
        assert_eq!(value.ttl_seconds, Some(0));
        assert!(!value.reorder);
    }

    #[test]
    fn computes_provider_default_and_stable_partition() {
        let messages = vec![
            json!({"role":"user","content":"volatile"}),
            json!({"role":"system","content":"stable","timestamp":1}),
        ];
        let plan = build_plan(
            &messages,
            &Inputs {
                source: "unused".to_owned(),
                provider: " Anthropic ".to_owned(),
                model: "m".to_owned(),
                ttl_seconds: None,
                reorder: true,
            },
            1000.0,
        )
        .expect("plan");
        assert_eq!(plan["provider"], "anthropic");
        assert_eq!(plan["ttl_seconds"], 300);
        assert_eq!(plan["stable_messages"], 1);
        assert_eq!(plan["volatile_messages"], 1);
        assert_eq!(plan["expires_at"], 1300.0);
        assert_eq!(plan["refresh_after"], 1225.0);
        assert_eq!(plan["reordered"], true);
        assert_eq!(plan["segments"][0]["stable"], true);
    }
}