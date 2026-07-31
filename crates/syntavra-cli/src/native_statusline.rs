#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};

#[derive(Clone, Copy)]
struct Mode {
    name: &'static str,
    description: &'static str,
    output_budget_bytes: u64,
    context_budget_tokens: u64,
    schema_profile: &'static str,
    rewrite_commands: bool,
    cache_optimize: bool,
    memory_extract: bool,
    auto_delegate: bool,
    style: &'static str,
}

const MODES: [Mode; 6] = [
    Mode {
        name: "full",
        description: "Balanced default with every exact-preserving optimizer enabled.",
        output_budget_bytes: 24_000,
        context_budget_tokens: 8_000,
        schema_profile: "balanced",
        rewrite_commands: true,
        cache_optimize: true,
        memory_extract: true,
        auto_delegate: true,
        style: "normal",
    },
    Mode {
        name: "lite",
        description: "Conservative compression with minimal behavior change.",
        output_budget_bytes: 48_000,
        context_budget_tokens: 4_000,
        schema_profile: "balanced",
        rewrite_commands: true,
        cache_optimize: true,
        memory_extract: false,
        auto_delegate: false,
        style: "normal",
    },
    Mode {
        name: "ultra",
        description: "Codex-oriented maximum context economy with exact recovery handles.",
        output_budget_bytes: 8_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: true,
        cache_optimize: true,
        memory_extract: true,
        auto_delegate: true,
        style: "terse",
    },
    Mode {
        name: "commit",
        description: "Small diff/status surface for commit preparation.",
        output_budget_bytes: 12_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: true,
        cache_optimize: true,
        memory_extract: false,
        auto_delegate: false,
        style: "commit",
    },
    Mode {
        name: "review",
        description: "Evidence-rich code review with bounded output.",
        output_budget_bytes: 32_000,
        context_budget_tokens: 3_000,
        schema_profile: "balanced",
        rewrite_commands: true,
        cache_optimize: true,
        memory_extract: true,
        auto_delegate: true,
        style: "review",
    },
    Mode {
        name: "compress",
        description: "Output-only compression; routing and delegation disabled.",
        output_budget_bytes: 10_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: false,
        cache_optimize: false,
        memory_extract: false,
        auto_delegate: false,
        style: "terse",
    },
];

fn mode_json(mode: Mode) -> Value {
    json!({
        "name": mode.name,
        "description": mode.description,
        "output_budget_bytes": mode.output_budget_bytes,
        "context_budget_tokens": mode.context_budget_tokens,
        "schema_profile": mode.schema_profile,
        "rewrite_commands": mode.rewrite_commands,
        "cache_optimize": mode.cache_optimize,
        "memory_extract": mode.memory_extract,
        "auto_delegate": mode.auto_delegate,
        "style": mode.style,
    })
}

fn normalize_mode(value: &str) -> Option<&'static str> {
    let normalized = value.trim().to_lowercase();
    let canonical = match normalized.as_str() {
        "default" | "balanced" => "full",
        "tiny" | "codex-ultra" | "codex_ultra" => "ultra",
        "off" => "lite",
        value => value,
    };
    MODES
        .iter()
        .find(|mode| mode.name == canonical)
        .map(|mode| mode.name)
}

fn current_mode(state_root: &Path) -> Result<Mode, String> {
    let path = state_root.join("optimization-mode.json");
    if !path.is_file() {
        return Ok(MODES[0]);
    }
    let value: Value = serde_json::from_slice(
        &fs::read(&path).map_err(|error| format!("STATUSLINE_MODE_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("STATUSLINE_MODE_JSON_INVALID:{error}"))?;
    let selected = value
        .get("mode")
        .and_then(Value::as_str)
        .and_then(normalize_mode)
        .unwrap_or("full");
    Ok(MODES
        .iter()
        .copied()
        .find(|mode| mode.name == selected)
        .unwrap_or(MODES[0]))
}

fn integer(value: Option<&Value>) -> i64 {
    value
        .and_then(Value::as_i64)
        .or_else(|| value.and_then(Value::as_u64).and_then(|item| i64::try_from(item).ok()))
        .or_else(|| value.and_then(Value::as_f64).map(|item| item as i64))
        .unwrap_or(0)
}

fn number(value: Option<&Value>) -> f64 {
    value.and_then(Value::as_f64).unwrap_or(0.0)
}

fn source_name(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) if !value.is_empty() => value.clone(),
        Some(Value::Bool(value)) => {
            if *value { "True" } else { "False" }.to_owned()
        }
        Some(Value::Number(value)) => value.to_string(),
        _ => "unknown".to_owned(),
    }
}

fn savings_summary(state_root: &Path) -> Result<Value, String> {
    let path = state_root.join("analytics").join("savings.jsonl");
    let mut rows = Vec::new();
    if path.is_file() {
        let text = fs::read_to_string(&path)
            .map_err(|error| format!("STATUSLINE_SAVINGS_READ_FAILED:{error}"))?;
        for line in text.lines() {
            if let Ok(Value::Object(row)) = serde_json::from_str::<Value>(line) {
                rows.push(row);
            }
        }
    }

    let original = rows
        .iter()
        .map(|row| integer(row.get("original_tokens")))
        .sum::<i64>();
    let visible = rows
        .iter()
        .map(|row| integer(row.get("visible_tokens")))
        .sum::<i64>();
    let saved = rows
        .iter()
        .map(|row| integer(row.get("saved_tokens")))
        .sum::<i64>();
    let before = rows
        .iter()
        .filter(|row| !matches!(row.get("provider_cost_before"), None | Some(Value::Null)))
        .map(|row| number(row.get("provider_cost_before")))
        .sum::<f64>();
    let after = rows
        .iter()
        .filter(|row| !matches!(row.get("provider_cost_after"), None | Some(Value::Null)))
        .map(|row| number(row.get("provider_cost_after")))
        .sum::<f64>();
    let mut by_source = BTreeMap::<String, i64>::new();
    for row in &rows {
        *by_source.entry(source_name(row.get("source"))).or_default() +=
            integer(row.get("saved_tokens"));
    }
    let by_source = by_source
        .into_iter()
        .map(|(key, value)| (key, json!(value)))
        .collect::<Map<String, Value>>();

    Ok(json!({
        "events": rows.len(),
        "original_tokens": original,
        "visible_tokens": visible,
        "saved_tokens": saved,
        "savings_ratio": if original == 0 { 0.0 } else { saved as f64 / original as f64 },
        "provider_cost_before": before,
        "provider_cost_after": after,
        "provider_cost_saved": (before - after).max(0.0),
        "by_source": by_source,
    }))
}

fn compact_saved(saved: i64) -> String {
    if saved >= 1_000_000 {
        format!("{:.1}m", saved as f64 / 1_000_000.0)
    } else if saved >= 1_000 {
        format!("{:.1}k", saved as f64 / 1_000.0)
    } else {
        saved.to_string()
    }
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let mode = current_mode(state_root)?;
    let savings = savings_summary(state_root)?;
    let saved = savings
        .get("saved_tokens")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let cost = savings
        .get("provider_cost_saved")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let verbose = arguments.iter().any(|argument| argument == "--verbose");
    let statusline = if verbose {
        format!(
            "Syntavra mode={} saved_tokens={} saved_cost={cost:.6}",
            mode.name, saved
        )
    } else {
        let suffix = if cost > 0.0 {
            format!(" ${cost:.2}")
        } else {
            String::new()
        };
        format!(
            "[SYN:{}] ⇩{}{}",
            mode.name.to_uppercase(),
            compact_saved(saved),
            suffix
        )
    };
    Ok(json!({
        "statusline": statusline,
        "mode": {
            "active": mode_json(mode),
            "available": MODES.into_iter().map(mode_json).collect::<Vec<_>>(),
            "instant_switch": true,
        },
        "savings": savings,
    }))
}

#[cfg(test)]
mod tests {
    use super::{compact_saved, execute};
    use std::fs;

    #[test]
    fn empty_state_uses_full_mode() {
        let root = std::env::temp_dir().join(format!(
            "syntavra-statusline-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        let value = execute(&["run".to_owned(), "statusline".to_owned()], &root)
            .expect("statusline");
        assert_eq!(value["statusline"], "[SYN:FULL] ⇩0");
        assert_eq!(value["mode"]["active"]["name"], "full");
        assert_eq!(value["savings"]["events"], 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn formats_saved_token_thresholds() {
        assert_eq!(compact_saved(999), "999");
        assert_eq!(compact_saved(1_000), "1.0k");
        assert_eq!(compact_saved(1_000_000), "1.0m");
    }
}
