#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;
const MAX_FILE_BYTES: u64 = 1024 * 1024;
const MAX_RECEIPT_WIRE_BYTES: usize = 64 * 1024;

const ROUTES: &[&str] = &[
    "receipt.inspect",
    "state.broker-live-snapshot",
    "state.broker-snapshot",
    "state.inspect",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [engine, route, name]
        if engine == "engine" && route == "route" && ROUTES.contains(&name.as_str()))
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let value = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(value) = value {
            if found.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            found = Some(value);
        }
        index += 1;
    }
    Ok(found)
}

fn flag_present(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn selection() -> Value {
    json!({
        "auto_policy": "python",
        "fallback_policy": "fail-closed",
        "reason": "EXPLICIT_SELECTION",
        "requested": "rust",
        "resolved": "rust",
        "schema_version": 1,
        "scope": "command",
        "source": "--engine",
        "source_path": "",
    })
}

fn envelope(command: &str, input: Value, result: Value) -> Value {
    json!({
        "ok": true,
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "capability": command,
        "mutation": "read-only",
        "selection": selection(),
        "input": input,
        "fallback": {"policy": "none", "attempted": false},
        "result": result,
    })
}

fn conflicting_input(arguments: &[String], allowed: &str) -> Result<bool, String> {
    for flag in [
        "--config-wire-hex",
        "--session-override-json-hex",
        "--task-override-json-hex",
        "--receipt-wire-hex",
        "--database-path",
        "--explain-path",
        "--scheduler-limit",
        "--migration-database",
    ] {
        if flag != allowed && option_value(arguments, flag)?.is_some() {
            return Ok(true);
        }
    }
    Ok(flag_present(arguments, "--live-config")
        || flag_present(arguments, "--telemetry-prometheus")
        || arguments
            .iter()
            .any(|value| value == "--scheduler-state" || value.starts_with("--scheduler-state=")))
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        _ => None,
    }
}

fn decode_receipt_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.is_empty()
        || value.len() > MAX_RECEIPT_WIRE_BYTES.saturating_mul(2)
        || value.len() % 2 != 0
    {
        return Err("RECEIPT_ROUTE_HEX_NONCANONICAL".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high =
            hex_nibble(pair[0]).ok_or_else(|| "RECEIPT_ROUTE_HEX_NONCANONICAL".to_owned())?;
        let low = hex_nibble(pair[1]).ok_or_else(|| "RECEIPT_ROUTE_HEX_NONCANONICAL".to_owned())?;
        output.push((high << 4) | low);
    }
    Ok(output)
}

fn project_identity(project_root: &Path) -> Result<(String, String), String> {
    let project = project_root.to_string_lossy().into_owned();
    let project_id = super::state_snapshot_contract::project_id_for_root(&project)?;
    Ok((project, project_id))
}

fn execute_subengine(arguments: &[String]) -> Result<Value, String> {
    super::native_rust_subprocess::execute_json(arguments)
}

fn state_inspect(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if conflicting_input(arguments, "")? {
        return Err("ENGINE_ROUTE_STATE_INSPECT_INPUT_UNSUPPORTED_R18".to_owned());
    }
    let (project, project_id) = project_identity(project_root)?;
    let rendered = super::state_snapshot_contract::inspect_state_root_json(&project, &project_id)?;
    let result: Value = serde_json::from_str(&rendered)
        .map_err(|_| "ENGINE_ROUTE_STATE_INSPECT_RESULT_INVALID".to_owned())?;
    let mut value = envelope(
        "state.inspect",
        json!({
            "profile": "project-bound-state-root-v1",
            "format": "sha256-normalized-absolute-path-v1",
            "bytes": 32,
            "sha256": project_id,
        }),
        result,
    );
    value
        .as_object_mut()
        .ok_or_else(|| "ENGINE_ROUTE_STATE_INSPECT_ENVELOPE_INVALID".to_owned())?
        .insert(
            "limits".to_owned(),
            json!({"maximum_file_bytes": MAX_FILE_BYTES}),
        );
    Ok(value)
}

fn receipt_inspect(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if conflicting_input(arguments, "--receipt-wire-hex")? {
        return Err("ENGINE_ROUTE_RECEIPT_INPUT_CONFLICT_R19".to_owned());
    }
    let encoded = option_value(arguments, "--receipt-wire-hex")?
        .ok_or_else(|| "ENGINE_ROUTE_RECEIPT_INPUT_REQUIRED_R19".to_owned())?;
    let wire = decode_receipt_hex(&encoded)?;
    let (_, project_id) = project_identity(project_root)?;
    let result = execute_subengine(&[
        "receipt".to_owned(),
        "inspect".to_owned(),
        project_id,
        encoded,
    ])?;
    Ok(envelope(
        "receipt.inspect",
        json!({
            "profile": "project-bound-receipt-wire-v1",
            "format": "R7RCPT1-lowercase-hex-v1",
            "bytes": wire.len(),
            "sha256": sha256_hex(&wire),
        }),
        result,
    ))
}

fn broker_snapshot(
    route: &str,
    arguments: &[String],
    project_root: &Path,
) -> Result<Value, String> {
    if conflicting_input(arguments, "--database-path")? {
        return Err("ENGINE_ROUTE_BROKER_INPUT_CONFLICT_R20".to_owned());
    }
    let database = option_value(arguments, "--database-path")?
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            if route == "state.broker-live-snapshot" {
                "ENGINE_ROUTE_BROKER_LIVE_DATABASE_INPUT_REQUIRED_R21".to_owned()
            } else {
                "ENGINE_ROUTE_BROKER_DATABASE_INPUT_REQUIRED_R20".to_owned()
            }
        })?;
    let (project, project_id) = project_identity(project_root)?;
    let operation = if route == "state.broker-live-snapshot" {
        "broker-live-snapshot"
    } else {
        "broker-snapshot"
    };
    let result = execute_subengine(&[
        "state".to_owned(),
        operation.to_owned(),
        project_id.clone(),
        project,
        database,
    ])?;
    let relative_path = result
        .get("database")
        .and_then(Value::as_object)
        .and_then(|database| database.get("relative_path"))
        .and_then(Value::as_str)
        .ok_or_else(|| "ENGINE_ROUTE_BROKER_RELATIVE_PATH_MISSING".to_owned())?;
    let material = format!("{project_id}\n{relative_path}\n");
    let (profile, format) = if route == "state.broker-live-snapshot" {
        (
            "project-bound-bounded-live-broker-sqlite-v1",
            "project-id-and-relative-live-broker-path-v1",
        )
    } else {
        (
            "project-bound-quiescent-broker-sqlite-v1",
            "project-id-and-relative-broker-path-v1",
        )
    };
    Ok(envelope(
        route,
        json!({
            "profile": profile,
            "format": format,
            "bytes": material.len(),
            "sha256": sha256_hex(material.as_bytes()),
        }),
        result,
    ))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
) -> Result<Value, String> {
    match command.get(2).map(String::as_str) {
        Some("state.inspect") => state_inspect(arguments, project_root),
        Some("receipt.inspect") => receipt_inspect(arguments, project_root),
        Some("state.broker-snapshot") => {
            broker_snapshot("state.broker-snapshot", arguments, project_root)
        }
        Some("state.broker-live-snapshot") => {
            broker_snapshot("state.broker-live-snapshot", arguments, project_root)
        }
        _ => Err("ENGINE_ROUTE_STATE_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn recognizes_complete_state_route_family() {
        for route in [
            "receipt.inspect",
            "state.broker-live-snapshot",
            "state.broker-snapshot",
            "state.inspect",
        ] {
            let command = ["engine", "route", route]
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            assert!(supports(&command));
        }
    }
}
