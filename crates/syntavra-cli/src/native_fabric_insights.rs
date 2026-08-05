#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use serde_json::{json, Map, Value};

#[derive(Debug)]
struct Event {
    event_type: String,
    family: String,
    host: String,
    raw_bytes: i64,
    visible_bytes: i64,
    latency_ms: f64,
    success: i64,
    cache_hit: i64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "insights")
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            index += 1;
            found = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{name}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|error| format!("FABRIC_INSIGHTS_CLOCK_FAILED:{error}"))
}

fn read_events(
    connection: &Connection,
    since_seconds: Option<f64>,
) -> Result<Vec<Event>, String> {
    let mut events = Vec::new();
    if let Some(seconds) = since_seconds {
        let cutoff = now()? - seconds.max(0.0);
        let mut statement = connection
            .prepare(
                "SELECT event_type,family,host,raw_bytes,visible_bytes,latency_ms,success,cache_hit \
                 FROM fabric_events WHERE created_at>=? ORDER BY event_id",
            )
            .map_err(|error| format!("FABRIC_INSIGHTS_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([cutoff], event_from_row)
            .map_err(|error| format!("FABRIC_INSIGHTS_QUERY_FAILED:{error}"))?;
        for row in rows {
            events.push(row.map_err(|error| format!("FABRIC_INSIGHTS_ROW_FAILED:{error}"))?);
        }
    } else {
        let mut statement = connection
            .prepare(
                "SELECT event_type,family,host,raw_bytes,visible_bytes,latency_ms,success,cache_hit \
                 FROM fabric_events ORDER BY event_id",
            )
            .map_err(|error| format!("FABRIC_INSIGHTS_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], event_from_row)
            .map_err(|error| format!("FABRIC_INSIGHTS_QUERY_FAILED:{error}"))?;
        for row in rows {
            events.push(row.map_err(|error| format!("FABRIC_INSIGHTS_ROW_FAILED:{error}"))?);
        }
    }
    Ok(events)
}

fn event_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Event> {
    Ok(Event {
        event_type: row.get(0)?,
        family: row.get(1)?,
        host: row.get(2)?,
        raw_bytes: row.get(3)?,
        visible_bytes: row.get(4)?,
        latency_ms: row.get(5)?,
        success: row.get(6)?,
        cache_hit: row.get(7)?,
    })
}

fn percentile(values: &[f64], fraction: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let raw = (ordered.len() as f64 * fraction).ceil() as usize;
    let index = raw.saturating_sub(1).min(ordered.len() - 1);
    ordered[index]
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sum = 0.0;
    let mut compensation = 0.0;
    for value in values {
        let candidate = sum + value;
        if sum.abs() >= value.abs() {
            compensation += (sum - candidate) + value;
        } else {
            compensation += (value - candidate) + sum;
        }
        sum = candidate;
    }
    (sum + compensation) / values.len() as f64
}

fn most_common<'a>(values: impl IntoIterator<Item = &'a str>) -> Value {
    let mut counts = BTreeMap::<String, (u64, usize)>::new();
    let mut next_index = 0usize;
    for value in values {
        let entry = counts.entry(value.to_owned()).or_insert_with(|| {
            let index = next_index;
            next_index += 1;
            (0, index)
        });
        entry.0 += 1;
    }
    let mut ordered = counts.into_iter().collect::<Vec<_>>();
    ordered.sort_by(|left, right| {
        right
            .1
             .0
            .cmp(&left.1 .0)
            .then_with(|| left.1 .1.cmp(&right.1 .1))
    });
    let mut result = Map::new();
    for (key, (count, _)) in ordered {
        result.insert(key, json!(count));
    }
    Value::Object(result)
}

fn metrics(connection: &Connection, since_seconds: Option<f64>) -> Result<Value, String> {
    let events = read_events(connection, since_seconds)?;
    let raw_bytes = events.iter().map(|event| event.raw_bytes).sum::<i64>();
    let visible_bytes = events
        .iter()
        .map(|event| event.visible_bytes)
        .sum::<i64>();
    let latencies = events
        .iter()
        .map(|event| event.latency_ms)
        .collect::<Vec<_>>();
    let successes = events.iter().map(|event| event.success).sum::<i64>();
    let cache_hits = events.iter().map(|event| event.cache_hit).sum::<i64>();
    let event_count = events.len();
    let success_rate = if event_count == 0 {
        1.0
    } else {
        successes as f64 / event_count as f64
    };
    let cache_hit_rate = if event_count == 0 {
        0.0
    } else {
        cache_hits as f64 / event_count as f64
    };
    let savings_ratio = if raw_bytes == 0 {
        0.0
    } else {
        (1.0 - visible_bytes as f64 / raw_bytes as f64).max(0.0)
    };
    let maximum_latency = latencies.iter().copied().fold(0.0_f64, f64::max);
    let integrity = super::native_fabric_doctor::database_integrity(connection)?;

    Ok(json!({
        "events": event_count,
        "success_rate": success_rate,
        "cache_hit_rate": cache_hit_rate,
        "raw_bytes": raw_bytes,
        "visible_bytes": visible_bytes,
        "saved_bytes": (raw_bytes - visible_bytes).max(0),
        "savings_ratio": savings_ratio,
        "latency_ms": {
            "mean": mean(&latencies),
            "p50": percentile(&latencies, 0.50),
            "p95": percentile(&latencies, 0.95),
            "max": maximum_latency,
        },
        "families": most_common(events.iter().map(|event| event.family.as_str())),
        "event_types": most_common(events.iter().map(|event| event.event_type.as_str())),
        "hosts": most_common(events.iter().map(|event| event.host.as_str())),
        "database_integrity": integrity,
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let since_seconds = option_value(arguments, "--since-seconds")?
        .map(|value| {
            value
                .parse::<f64>()
                .map_err(|error| format!("--since-seconds_INVALID:{error}"))
        })
        .transpose()?;
    let connection = super::native_fabric_doctor::open_database(
        &state_root.join("competitive-fabric.sqlite3"),
    )?;
    let value = metrics(&connection, since_seconds)?;
    option_value(arguments, "--output")?.map_or_else(
        || Ok(value.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &value),
    )
}

#[cfg(test)]
mod tests {
    use super::{mean, percentile, supports};

    #[test]
    fn routes_fabric_insights_only() {
        assert!(supports(&["fabric".to_owned(), "insights".to_owned()]));
        assert!(!supports(&["fabric".to_owned(), "doctor".to_owned()]));
    }

    #[test]
    fn percentile_matches_nearest_rank_contract() {
        let values = [10.0, 20.0, 30.0, 40.0];
        assert_eq!(percentile(&values, 0.50), 20.0);
        assert_eq!(percentile(&values, 0.95), 40.0);
        assert_eq!(mean(&values), 25.0);
    }
}
