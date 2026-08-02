#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

const PROOF_WORKLOADS: [&str; 6] = [
    "coding-agent",
    "repository-task",
    "swe-bench",
    "oolong-long-context",
    "session-continuity",
    "tool-routing",
];

const ARMS: [&str; 14] = [
    "baseline",
    "plain-host",
    "syntavra",
    "syntavra-minimal",
    "syntavra-balanced",
    "caveman",
    "rtk",
    "token-savior",
    "jcodemunch",
    "full-competitor-pack",
    "context-mode",
    "headroom",
    "volt-lcm",
    "recursive",
];

fn schema() -> Value {
    json!({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://syntavra.dev/schemas/provider-usage-receipt-v1.json",
        "title": "Syntavra Provider Usage Receipt",
        "type": "object",
        "required": [
            "receipt_id",
            "provider",
            "model",
            "request_id",
            "session_id",
            "repository_hash",
            "integration_id",
            "observed_at",
            "wall_time_ms",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "cost_usd",
            "quality_score",
            "success",
            "synthetic",
            "raw_usage_hash",
            "workload",
            "arm",
            "task_id",
            "repetition",
        ],
        "properties": {
            "receipt_id": {"type": "string", "minLength": 1},
            "provider": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "request_id": {"type": "string", "minLength": 1},
            "session_id": {"type": "string", "minLength": 1},
            "repository_hash": {"type": "string", "minLength": 16},
            "integration_id": {"type": "string", "minLength": 1},
            "observed_at": {"type": "string", "format": "date-time"},
            "wall_time_ms": {"type": "number", "minimum": 0},
            "input_tokens": {"type": "integer", "minimum": 0},
            "cached_input_tokens": {"type": "integer", "minimum": 0},
            "output_tokens": {"type": "integer", "minimum": 0},
            "cost_usd": {"type": "number", "minimum": 0},
            "quality_score": {"type": "number", "minimum": 0, "maximum": 1},
            "success": {"type": "boolean"},
            "synthetic": {"type": "boolean"},
            "raw_usage_hash": {"type": "string", "minLength": 32},
            "workload": {"enum": PROOF_WORKLOADS},
            "arm": {"enum": ARMS},
            "task_id": {"type": "string", "minLength": 1},
            "repetition": {"type": "integer", "minimum": 1},
            "metadata": {"type": "object"},
        },
        "additionalProperties": true,
    })
}

fn output_path(arguments: &[String]) -> Result<PathBuf, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "prove" && window[1] == "schema")
        .map(|window| PathBuf::from(&window[2]))
        .ok_or_else(|| "PROVE_SCHEMA_OUTPUT_MISSING".to_owned())
}

fn write_schema(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent().filter(|parent| !parent.as_os_str().is_empty()) {
        fs::create_dir_all(parent)
            .map_err(|error| format!("PROVE_SCHEMA_PARENT_CREATE_FAILED:{error}"))?;
    }
    let mut encoded = serde_json::to_string_pretty(value)
        .map_err(|error| format!("PROVE_SCHEMA_SERIALIZE_FAILED:{error}"))?;
    encoded.push('\n');
    fs::write(path, encoded).map_err(|error| format!("PROVE_SCHEMA_WRITE_FAILED:{error}"))
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let path = output_path(arguments)?;
    let value = schema();
    write_schema(&path, &value)?;
    Ok(json!({"ok": true, "output": path, "schema": value}))
}

#[cfg(test)]
mod tests {
    use super::schema;

    #[test]
    fn schema_has_required_contract() {
        let value = schema();
        assert_eq!(value["type"], "object");
        assert_eq!(value["additionalProperties"], true);
        assert_eq!(value["properties"]["workload"]["enum"][0], "coding-agent");
        assert_eq!(value["properties"]["arm"]["enum"][13], "recursive");
    }
}
