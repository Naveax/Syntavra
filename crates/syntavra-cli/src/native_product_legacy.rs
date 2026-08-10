#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::env;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

#[path = "native_delegate.rs"]
mod native_delegate;
#[path = "native_provider_gateway_read.rs"]
mod native_provider_gateway_read;

const MAX_CACHE_STATE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_PROVIDER_INPUT_BYTES: usize = 4 * 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    command.len() == 2
        && ((command[0] == "run"
            && matches!(
                command[1].as_str(),
                "cache-health" | "delegate" | "provider-route"
            ))
            || (command[0] == "provider" && matches!(command[1].as_str(), "stats" | "verify")))
}

fn number_as_i64(value: Option<&Value>) -> i64 {
    value
        .and_then(|item| {
            item.as_i64()
                .or_else(|| item.as_u64().and_then(|number| i64::try_from(number).ok()))
        })
        .unwrap_or(0)
}

fn number_as_f64(value: Option<&Value>) -> f64 {
    value.and_then(Value::as_f64).unwrap_or(0.0)
}

fn health_from_plans(plans: Option<&Map<String, Value>>, now: f64) -> Value {
    let rows = plans.into_iter().flat_map(Map::values);
    let mut plans_count = 0usize;
    let mut active = 0usize;
    let mut refresh_due = 0usize;
    let mut expired = 0usize;
    let mut cacheable_tokens = 0i64;
    for row in rows {
        plans_count += 1;
        let refresh_after = number_as_f64(row.get("refresh_after"));
        let expires_at = number_as_f64(row.get("expires_at"));
        if refresh_after <= now && now < expires_at {
            refresh_due += 1;
        }
        if expires_at <= now {
            expired += 1;
        }
        if now < refresh_after {
            active += 1;
        }
        cacheable_tokens =
            cacheable_tokens.saturating_add(number_as_i64(row.get("cacheable_tokens")));
    }
    json!({
        "plans": plans_count,
        "active": active,
        "refresh_due": refresh_due,
        "expired": expired,
        "cacheable_tokens": cacheable_tokens,
    })
}

fn cache_health(state_root: &Path) -> Result<Value, String> {
    let path = state_root.join("cache").join("plans.json");
    let bytes = match fs::read(&path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => Vec::new(),
        Err(_) => return Err("CACHE_HEALTH_READ_FAILED".to_owned()),
    };
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_CACHE_STATE_BYTES {
        return Err("CACHE_HEALTH_STATE_TOO_LARGE".to_owned());
    }
    let root = if bytes.is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_slice::<Value>(&bytes)
            .map_err(|_| "CACHE_HEALTH_STATE_INVALID".to_owned())?
    };
    let now = unix_time()?;
    Ok(health_from_plans(
        root.get("plans").and_then(Value::as_object),
        now,
    ))
}

fn unix_time() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SYSTEM_CLOCK_INVALID".to_owned())
        .map(|duration| duration.as_secs_f64())
}

fn string_field(
    row: &Map<String, Value>,
    name: &str,
    default: Option<&str>,
) -> Result<String, String> {
    match row.get(name) {
        Some(Value::String(value)) => Ok(value.clone()),
        Some(_) => Err(format!("PROVIDER_CANDIDATE_{name}_INVALID")),
        None => default
            .map(ToOwned::to_owned)
            .ok_or_else(|| format!("PROVIDER_CANDIDATE_{name}_MISSING")),
    }
}

fn bool_field(row: &Map<String, Value>, name: &str, default: bool) -> Result<bool, String> {
    match row.get(name) {
        Some(Value::Bool(value)) => Ok(*value),
        Some(_) => Err(format!("PROVIDER_CANDIDATE_{name}_INVALID")),
        None => Ok(default),
    }
}

fn float_field(row: &Map<String, Value>, name: &str, default: f64) -> Result<f64, String> {
    let value = match row.get(name) {
        Some(value) => value
            .as_f64()
            .ok_or_else(|| format!("PROVIDER_CANDIDATE_{name}_INVALID"))?,
        None => default,
    };
    if value.is_finite() {
        Ok(value)
    } else {
        Err(format!("PROVIDER_CANDIDATE_{name}_NONFINITE"))
    }
}

fn integer_field(row: &Map<String, Value>, name: &str, default: i64) -> Result<i64, String> {
    match row.get(name) {
        Some(value) => value
            .as_i64()
            .or_else(|| value.as_u64().and_then(|number| i64::try_from(number).ok()))
            .ok_or_else(|| format!("PROVIDER_CANDIDATE_{name}_INVALID")),
        None => Ok(default),
    }
}

#[derive(Debug, Clone)]
struct ProviderCandidate {
    provider: String,
    model: String,
    available: bool,
    quota_remaining: f64,
    rate_limited_until: f64,
    input_cost_per_million: f64,
    output_cost_per_million: f64,
    latency_ms: f64,
    quality: f64,
    max_complexity: String,
    context_window: i64,
    account: String,
    subscription: bool,
    priority: i32,
}

impl ProviderCandidate {
    fn from_value(value: &Value) -> Result<Self, String> {
        let row = value
            .as_object()
            .ok_or_else(|| "PROVIDER_CANDIDATE_NOT_OBJECT".to_owned())?;
        let priority = integer_field(row, "priority", 0)?;
        Ok(Self {
            provider: string_field(row, "provider", None)?,
            model: string_field(row, "model", None)?,
            available: bool_field(row, "available", true)?,
            quota_remaining: float_field(row, "quota_remaining", 1.0)?,
            rate_limited_until: float_field(row, "rate_limited_until", 0.0)?,
            input_cost_per_million: float_field(row, "input_cost_per_million", 0.0)?,
            output_cost_per_million: float_field(row, "output_cost_per_million", 0.0)?,
            latency_ms: float_field(row, "latency_ms", 0.0)?,
            quality: float_field(row, "quality", 0.5)?,
            max_complexity: string_field(row, "max_complexity", Some("reasoning"))?,
            context_window: integer_field(row, "context_window", 0)?,
            account: string_field(row, "account", Some("default"))?,
            subscription: bool_field(row, "subscription", false)?,
            priority: i32::try_from(priority)
                .map_err(|_| "PROVIDER_CANDIDATE_priority_INVALID".to_owned())?,
        })
    }
}

fn complexity_rank(value: &str) -> i32 {
    match value {
        "medium" => 1,
        "complex" => 2,
        "reasoning" => 3,
        _ => 0,
    }
}

fn classify_complexity(task: &str, changed_files: i64, token_estimate: i64) -> &'static str {
    let corpus = task.to_lowercase();
    let mut score = 0;
    if [
        "architecture",
        "security",
        "migration",
        "race condition",
        "formal",
        "proof",
        "root cause",
    ]
    .iter()
    .any(|term| corpus.contains(term))
    {
        score += 2;
    }
    if ["refactor", "debug", "benchmark", "cross-repo", "dependency"]
        .iter()
        .any(|term| corpus.contains(term))
    {
        score += 1;
    }
    if changed_files >= 8 {
        score += 1;
    }
    if token_estimate >= 16_000 {
        score += 1;
    }
    match score {
        4.. => "reasoning",
        2..=3 => "complex",
        1 => "medium",
        _ => "simple",
    }
}

#[derive(Debug)]
struct RankedCandidate {
    score: f64,
    candidate: ProviderCandidate,
    reasons: Vec<String>,
}

fn compare_ranked(left: &RankedCandidate, right: &RankedCandidate) -> Ordering {
    right
        .score
        .total_cmp(&left.score)
        .then_with(|| left.candidate.provider.cmp(&right.candidate.provider))
        .then_with(|| left.candidate.model.cmp(&right.candidate.model))
        .then_with(|| left.candidate.account.cmp(&right.candidate.account))
}

fn provider_route_from_rows(
    task: &str,
    rows: &[Value],
    changed_files: i64,
    token_estimate: i64,
    now: f64,
) -> Result<Value, String> {
    let complexity = classify_complexity(task, changed_files, token_estimate);
    let needed = complexity_rank(complexity);
    let mut ranked = Vec::new();
    for value in rows {
        let candidate = ProviderCandidate::from_value(value)?;
        if !candidate.available
            || candidate.rate_limited_until > now
            || candidate.quota_remaining <= 0.0
            || complexity_rank(&candidate.max_complexity) < needed
            || (candidate.context_window != 0 && token_estimate > candidate.context_window)
        {
            continue;
        }
        let blended_cost =
            candidate.input_cost_per_million + candidate.output_cost_per_million * 2.0;
        let mut score =
            candidate.quality * 60.0 - blended_cost * 2.0 - candidate.latency_ms / 1000.0;
        score += (candidate.quota_remaining * 20.0).min(20.0);
        score += f64::from(candidate.priority.clamp(-20, 20)) * 0.5;
        let mut reasons = Vec::new();
        if candidate.subscription {
            score += 15.0;
            reasons.push("subscription-account".to_owned());
        } else if blended_cost == 0.0 {
            reasons.push("zero-priced-model".to_owned());
        }
        reasons.extend([
            format!("quality={:.3}", candidate.quality),
            format!("quota={:.3}", candidate.quota_remaining),
            format!("priority={}", candidate.priority),
            format!("latency_ms={:.1}", candidate.latency_ms),
            format!("cost_index={blended_cost:.4}"),
        ]);
        ranked.push(RankedCandidate {
            score,
            candidate,
            reasons,
        });
    }
    if ranked.is_empty() {
        return Err("NO_PROVIDER_SATISFIES_CONSTRAINTS".to_owned());
    }
    ranked.sort_by(compare_ranked);
    let selected = &ranked[0];
    let fallbacks = ranked
        .iter()
        .skip(1)
        .take(5)
        .map(|row| {
            json!([
                row.candidate.provider,
                row.candidate.model,
                row.candidate.account
            ])
        })
        .collect::<Vec<_>>();
    let body = json!({
        "provider": selected.candidate.provider,
        "model": selected.candidate.model,
        "account": selected.candidate.account,
        "complexity": complexity,
        "score": selected.score,
        "reasons": selected.reasons,
        "fallbacks": fallbacks,
    });
    let canonical =
        serde_json::to_vec(&body).map_err(|_| "PROVIDER_ROUTE_RENDER_FAILED".to_owned())?;
    let mut output = body
        .as_object()
        .cloned()
        .ok_or_else(|| "PROVIDER_ROUTE_RENDER_FAILED".to_owned())?;
    output.insert(
        "receipt_hash".to_owned(),
        Value::String(sha256_hex(&canonical)),
    );
    Ok(Value::Object(output))
}

fn load_json_argument(value: &str) -> Result<Value, String> {
    let path = Path::new(value);
    let bytes = if path.is_file() {
        fs::read(path).map_err(|_| "PROVIDER_CANDIDATES_READ_FAILED".to_owned())?
    } else {
        value.as_bytes().to_vec()
    };
    if bytes.len() > MAX_PROVIDER_INPUT_BYTES {
        return Err("PROVIDER_CANDIDATES_TOO_LARGE".to_owned());
    }
    serde_json::from_slice(&bytes).map_err(|_| "PROVIDER_CANDIDATES_INVALID_JSON".to_owned())
}

fn optional_integer_flag(arguments: &[String], flag: &str) -> Result<i64, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let raw = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_MISSING"))?
                    .as_str(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(value) = raw {
            if result.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            result = Some(
                value
                    .parse::<i64>()
                    .map_err(|_| format!("{flag}_INVALID"))?,
            );
        }
        index += 1;
    }
    Ok(result.unwrap_or(0))
}

fn provider_route(arguments: &[String]) -> Result<Value, String> {
    let action = arguments
        .iter()
        .position(|value| value == "provider-route")
        .ok_or_else(|| "PROVIDER_ROUTE_ACTION_MISSING".to_owned())?;
    let task = arguments
        .get(action + 1)
        .ok_or_else(|| "PROVIDER_ROUTE_TASK_MISSING".to_owned())?;
    let candidates_source = arguments
        .get(action + 2)
        .ok_or_else(|| "PROVIDER_ROUTE_CANDIDATES_MISSING".to_owned())?;
    let candidates = load_json_argument(candidates_source)?;
    let rows = candidates
        .as_array()
        .ok_or_else(|| "PROVIDER_CANDIDATES_NOT_ARRAY".to_owned())?;
    provider_route_from_rows(
        task,
        rows,
        optional_integer_flag(arguments, "--changed-files")?,
        optional_integer_flag(arguments, "--tokens")?,
        unix_time()?,
    )
}

pub fn execute(command: &[String], state_root: &Path) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    if command[0] == "provider" {
        return match command[1].as_str() {
            "stats" => native_provider_gateway_read::stats(state_root).map(Some),
            "verify" => native_provider_gateway_read::verify(state_root).map(Some),
            _ => Ok(None),
        };
    }
    match command[1].as_str() {
        "cache-health" => cache_health(state_root).map(Some),
        "delegate" => {
            let arguments = env::args().skip(1).collect::<Vec<_>>();
            native_delegate::execute(&arguments).map(Some)
        }
        "provider-route" => {
            let arguments = env::args().skip(1).collect::<Vec<_>>();
            provider_route(&arguments).map(Some)
        }
        _ => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{classify_complexity, health_from_plans, provider_route_from_rows};

    #[test]
    fn empty_cache_health_matches_python_shape() {
        assert_eq!(
            health_from_plans(None, 100.0),
            json!({
                "plans": 0,
                "active": 0,
                "refresh_due": 0,
                "expired": 0,
                "cacheable_tokens": 0,
            })
        );
    }

    #[test]
    fn cache_health_classifies_active_due_and_expired_plans() {
        let value = json!({
            "active": {"refresh_after": 120.0, "expires_at": 140.0, "cacheable_tokens": 10},
            "due": {"refresh_after": 90.0, "expires_at": 110.0, "cacheable_tokens": 20},
            "expired": {"refresh_after": 70.0, "expires_at": 80.0, "cacheable_tokens": 30}
        });
        assert_eq!(
            health_from_plans(value.as_object(), 100.0),
            json!({
                "plans": 3,
                "active": 1,
                "refresh_due": 1,
                "expired": 1,
                "cacheable_tokens": 60,
            })
        );
    }

    #[test]
    fn provider_complexity_matches_reference_thresholds() {
        assert_eq!(classify_complexity("rename variable", 1, 100), "simple");
        assert_eq!(classify_complexity("debug dependency", 1, 100), "medium");
        assert_eq!(
            classify_complexity("architecture migration", 1, 100),
            "complex"
        );
        assert_eq!(
            classify_complexity("architecture migration proof", 9, 20_000),
            "reasoning"
        );
    }

    #[test]
    fn provider_route_matches_reference_receipt() {
        let rows = json!([
            {
                "provider": "openai",
                "model": "gpt-x",
                "quality": 0.9,
                "quota_remaining": 0.8,
                "latency_ms": 100.0,
                "subscription": true,
                "priority": 4,
                "max_complexity": "reasoning",
                "context_window": 200_000
            },
            {
                "provider": "local",
                "model": "qwen",
                "quality": 0.7,
                "quota_remaining": 1.0,
                "latency_ms": 30.0,
                "priority": 1,
                "max_complexity": "complex",
                "context_window": 64_000
            }
        ]);
        assert_eq!(
            provider_route_from_rows(
                "architecture migration proof",
                rows.as_array().expect("array"),
                9,
                20_000,
                0.0,
            )
            .expect("route"),
            json!({
                "provider": "openai",
                "model": "gpt-x",
                "account": "default",
                "complexity": "reasoning",
                "score": 86.9,
                "reasons": [
                    "subscription-account",
                    "quality=0.900",
                    "quota=0.800",
                    "priority=4",
                    "latency_ms=100.0",
                    "cost_index=0.0000"
                ],
                "fallbacks": [],
                "receipt_hash": "fdd43691fd4a6ab7698345a0738f0ec2ded3c352da7509569800bc73714154a5"
            })
        );
    }
}
