#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::config_contract::{resolve_config_wire, snapshot_json};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;

const STATIC_ROUTES: &[&str] = &[
    "config.resolve",
    "pipeline.describe",
    "plugins.list",
    "state.layout",
    "telemetry.metrics",
    "version",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [engine, route, name]
        if engine == "engine" && route == "route" && STATIC_ROUTES.contains(&name.as_str()))
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

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("ENGINE_ROUTE_CONFIG_WIRE_HEX_INVALID".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = hex_nibble(pair[0])
            .ok_or_else(|| "ENGINE_ROUTE_CONFIG_WIRE_HEX_INVALID".to_owned())?;
        let low = hex_nibble(pair[1])
            .ok_or_else(|| "ENGINE_ROUTE_CONFIG_WIRE_HEX_INVALID".to_owned())?;
        output.push((high << 4) | low);
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
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

fn envelope(command: &str, capability: &str, input: Value, result: Value) -> Value {
    json!({
        "ok": true,
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "capability": capability,
        "mutation": "read-only",
        "selection": selection(),
        "input": input,
        "fallback": {"policy": "none", "attempted": false},
        "result": result,
    })
}

fn no_input() -> Value {
    json!({"profile": "none", "format": null, "bytes": 0, "sha256": null})
}

fn wire_input(wire: &[u8]) -> Value {
    json!({
        "profile": "explicit-config-wire-v1",
        "format": "R6CFG1",
        "bytes": wire.len(),
        "sha256": sha256_hex(wire),
    })
}

fn json_value(rendered: &str, code: &str) -> Result<Value, String> {
    serde_json::from_str(rendered).map_err(|_| code.to_owned())
}

fn route_version() -> Value {
    envelope(
        "version",
        "version",
        no_input(),
        json!({
            "contract_version": 1,
            "engine": "rust",
            "engine_stability": "experimental",
            "product": "Syntavra",
            "product_version": "0.0.1",
            "release_channel": "pre-release",
        }),
    )
}

fn route_config_resolve(arguments: &[String]) -> Result<Value, String> {
    let encoded = option_value(arguments, "--config-wire-hex")?
        .ok_or_else(|| "ENGINE_ROUTE_INPUT_REQUIRED_R14".to_owned())?;
    if flag_present(arguments, "--live-config") {
        return Err("ENGINE_ROUTE_CONFIG_INPUT_CONFLICT".to_owned());
    }
    let wire = decode_hex(&encoded)?;
    let snapshot = resolve_config_wire(&wire)?;
    let result = json_value(
        &snapshot_json(&snapshot)?,
        "ENGINE_ROUTE_CONFIG_JSON_INVALID",
    )?;
    Ok(envelope(
        "config.resolve",
        "config.resolve",
        wire_input(&wire),
        result,
    ))
}

fn route_static(command: &str) -> Result<Value, String> {
    let rendered = super::read_only_cli_contract::result_json(command)?;
    let result = json_value(&rendered, "ENGINE_ROUTE_STATIC_RESULT_INVALID")?;
    Ok(envelope(command, command, no_input(), result))
}

fn route_state_layout() -> Result<Value, String> {
    let result = json_value(
        super::state_layout_contract::state_layout_json(),
        "ENGINE_ROUTE_STATE_LAYOUT_INVALID",
    )?;
    Ok(envelope(
        "state.layout",
        "state.layout",
        no_input(),
        result,
    ))
}

fn route_telemetry(arguments: &[String]) -> Result<Value, String> {
    let prometheus = flag_present(arguments, "--telemetry-prometheus");
    let output_format = if prometheus { "prometheus" } else { "json" };
    let rendered = super::telemetry_metrics_contract::telemetry_metrics_json(output_format)?;
    let result = json_value(
        &rendered,
        "ENGINE_ROUTE_TELEMETRY_RESULT_INVALID",
    )?;
    let request = format!(
        r#"{{"format":"{output_format}","route":"telemetry.metrics"}}"#
    );
    Ok(envelope(
        "telemetry.metrics",
        "telemetry.metrics",
        json!({
            "profile": "process-local-empty-metrics-v1",
            "format": "canonical-output-format",
            "bytes": request.len(),
            "sha256": sha256_hex(request.as_bytes()),
        }),
        result,
    ))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    _project_root: &Path,
) -> Result<Value, String> {
    let route = command
        .get(2)
        .map(String::as_str)
        .ok_or_else(|| "ENGINE_ROUTE_COMMAND_MISSING".to_owned())?;
    match route {
        "version" => Ok(route_version()),
        "config.resolve" => route_config_resolve(arguments),
        "pipeline.describe" | "plugins.list" => route_static(route),
        "state.layout" => route_state_layout(),
        "telemetry.metrics" => route_telemetry(arguments),
        _ => Err("ENGINE_ROUTE_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{execute, supports};
    use std::path::Path;

    fn command(route: &str) -> Vec<String> {
        ["engine", "route", route]
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn recognizes_certified_static_routes() {
        assert!(supports(&command("version")));
        assert!(supports(&command("state.layout")));
        assert!(!supports(&command("config.show")));
    }

    #[test]
    fn version_route_is_native_rust() {
        let arguments = ["engine", "route", "version"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let value = execute(&command("version"), &arguments, Path::new("."))
            .expect("version route");
        assert_eq!(value["result"]["engine"], "rust");
        assert_eq!(value["selection"]["resolved"], "rust");
    }
}
