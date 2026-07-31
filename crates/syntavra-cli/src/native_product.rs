#![forbid(unsafe_code)]

use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

const MAX_CACHE_STATE_BYTES: u64 = 16 * 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    command.len() == 2 && command[0] == "run" && command[1] == "cache-health"
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
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "CACHE_HEALTH_CLOCK_INVALID".to_owned())?
        .as_secs_f64();
    Ok(health_from_plans(
        root.get("plans").and_then(Value::as_object),
        now,
    ))
}

pub fn execute(command: &[String], state_root: &Path) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    cache_health(state_root).map(Some)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::health_from_plans;

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
}
