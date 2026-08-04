#![forbid(unsafe_code)]

use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const MAX_EVENT_BYTES: u64 = 16 * 1024 * 1024;
const ALLOWED_FIELDS: &[&str] = &[
    "event_id",
    "observed_at",
    "session_id",
    "repository_hash",
    "kind",
    "provider",
    "model",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "wall_time_ms",
    "cost_usd",
    "quality_score",
    "success",
    "compaction_ms",
    "continuity_restored",
    "tool_route_allowed",
    "metadata",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "record")
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "ANALYTICS_EVENT_SERIALIZE_FAILED".to_owned())
}

fn load_event(source: &str) -> Result<Value, String> {
    let path = Path::new(source);
    let bytes = if path.is_file() {
        let metadata =
            fs::symlink_metadata(path).map_err(|_| "ANALYTICS_EVENT_INSPECT_FAILED".to_owned())?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err("ANALYTICS_EVENT_SOURCE_INVALID".to_owned());
        }
        if metadata.len() > MAX_EVENT_BYTES {
            return Err("ANALYTICS_EVENT_TOO_LARGE".to_owned());
        }
        fs::read(path).map_err(|_| "ANALYTICS_EVENT_READ_FAILED".to_owned())?
    } else {
        if u64::try_from(source.len()).unwrap_or(u64::MAX) > MAX_EVENT_BYTES {
            return Err("ANALYTICS_EVENT_TOO_LARGE".to_owned());
        }
        source.as_bytes().to_vec()
    };
    let value: Value =
        serde_json::from_slice(&bytes).map_err(|_| "ANALYTICS_EVENT_JSON_INVALID".to_owned())?;
    if !value.is_object() {
        return Err("ANALYTICS_EVENT_OBJECT_REQUIRED".to_owned());
    }
    Ok(value)
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    let month = u32::try_from(month).expect("civil month is within 1..=12");
    let day = u32::try_from(day).expect("civil day is within 1..=31");
    (year, month, day)
}

fn utc_isoformat() -> Result<String, String> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "ANALYTICS_SYSTEM_CLOCK_INVALID".to_owned())?;
    let seconds = i64::try_from(elapsed.as_secs())
        .map_err(|_| "ANALYTICS_SYSTEM_CLOCK_OVERFLOW".to_owned())?;
    let days = seconds.div_euclid(86_400);
    let within_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = within_day / 3_600;
    let minute = (within_day % 3_600) / 60;
    let second = within_day % 60;
    let micros = elapsed.subsec_micros();
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00"
    ))
}

fn source_argument(arguments: &[String]) -> Result<&str, String> {
    let index = arguments
        .iter()
        .position(|value| value == "record")
        .ok_or_else(|| "ANALYTICS_RECORD_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| "ANALYTICS_EVENT_MISSING".to_owned())
}

pub fn record_event(event: &Value, state_root: &Path) -> Result<Value, String> {
    let default_event_id = sha256_hex(&canonical_bytes(event)?);
    let event_object = event
        .as_object()
        .ok_or_else(|| "ANALYTICS_EVENT_OBJECT_REQUIRED".to_owned())?;
    let mut row = Map::new();
    for field in ALLOWED_FIELDS {
        if let Some(value) = event_object.get(*field) {
            row.insert((*field).to_owned(), value.clone());
        }
    }
    row.entry("event_id".to_owned())
        .or_insert_with(|| Value::String(default_event_id));
    if !row.contains_key("observed_at") {
        row.insert("observed_at".to_owned(), Value::String(utc_isoformat()?));
    }
    row.entry("kind".to_owned())
        .or_insert_with(|| Value::String("agent-turn".to_owned()));
    row.insert("schema_version".to_owned(), Value::Number(1_u64.into()));

    let path = state_root.join("analytics").join("events.jsonl");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|_| "ANALYTICS_DIRECTORY_CREATE_FAILED".to_owned())?;
    }
    let encoded = canonical_bytes(&Value::Object(row.clone()))?;
    let mut handle = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|_| "ANALYTICS_EVENT_OPEN_FAILED".to_owned())?;
    handle
        .write_all(&encoded)
        .and_then(|()| handle.write_all(b"\n"))
        .map_err(|_| "ANALYTICS_EVENT_WRITE_FAILED".to_owned())?;

    Ok(json!({
        "ok": true,
        "event_id": row
            .get("event_id")
            .cloned()
            .ok_or_else(|| "ANALYTICS_EVENT_ID_MISSING".to_owned())?,
        "path": path,
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let event = load_event(source_argument(arguments)?)?;
    record_event(&event, state_root)
}

#[cfg(test)]
mod tests {
    use super::{civil_from_days, supports};

    #[test]
    fn epoch_day_is_1970_01_01() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
    }

    #[test]
    fn record_command_is_supported() {
        let command = ["run", "record"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert!(supports(&command));
    }
}
