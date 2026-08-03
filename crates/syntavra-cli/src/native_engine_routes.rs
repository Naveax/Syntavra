#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;

const STATIC_ROUTES: &[&str] = &[
    "config.resolve",
    "config.show",
    "config.validate",
    "pipeline.describe",
    "plugins.list",
    "state.layout",
    "status",
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

fn wire_input(wire: &[u8], profile: &str) -> Value {
    json!({
        "profile": profile,
        "format": "R6CFG1",
        "bytes": wire.len(),
        "sha256": sha256_hex(wire),
    })
}

fn json_value(rendered: &str, code: &str) -> Result<Value, String> {
    serde_json::from_str(rendered).map_err(|_| code.to_owned())
}

fn config_wire_for_status(
    arguments: &[String],
    project_root: &Path,
) -> Result<(Vec<u8>, &'static str), String> {
    if let Some(value) = option_value(arguments, "--config-wire-hex")? {
        return Ok((decode_hex(&value)?, "explicit-config-wire-v1"));
    }
    if flag_present(arguments, "--live-config") {
        return Ok((
            super::native_config_read_only::discover_wire(project_root)?,
            "live-config-discovery-v1",
        ));
    }
    Ok((default_config_wire().to_vec(), "default-config-only"))
}

fn config_snapshot(
    wire: &[u8],
) -> Result<(Value, super::config_contract::ConfigSnapshot), String> {
    let snapshot = resolve_config_wire(wire)?;
    let value = json_value(
        &snapshot_json(&snapshot)?,
        "ENGINE_ROUTE_CONFIG_JSON_INVALID",
    )?;
    Ok((value, snapshot))
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

fn route_status(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    let (wire, profile) = config_wire_for_status(arguments, project_root)?;
    let snapshot = resolve_config_wire(&wire)?;
    let result = json_value(
        &status_json(&snapshot),
        "ENGINE_ROUTE_STATUS_JSON_INVALID",
    )?;
    Ok(envelope(
        "status",
        "status",
        wire_input(&wire, profile),
        result,
    ))
}

fn route_config_resolve(arguments: &[String]) -> Result<Value, String> {
    let encoded = option_value(arguments, "--config-wire-hex")?
        .ok_or_else(|| "ENGINE_ROUTE_INPUT_REQUIRED_R14".to_owned())?;
    if flag_present(arguments, "--live-config") {
        return Err("ENGINE_ROUTE_CONFIG_INPUT_CONFLICT".to_owned());
    }
    let wire = decode_hex(&encoded)?;
    let (result, _) = config_snapshot(&wire)?;
    Ok(envelope(
        "config.resolve",
        "config.resolve",
        wire_input(&wire, "explicit-config-wire-v1"),
        result,
    ))
}

fn route_config_show(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if option_value(arguments, "--config-wire-hex")?.is_some()
        || flag_present(arguments, "--live-config")
    {
        return Err("ENGINE_ROUTE_CONFIG_SHOW_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let wire = super::native_config_read_only::discover_wire(project_root)?;
    let (result, _) = config_snapshot(&wire)?;
    Ok(envelope(
        "config.show",
        "config.show",
        wire_input(&wire, "live-config-discovery-v1"),
        result,
    ))
}

fn route_config_validate(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if option_value(arguments, "--config-wire-hex")?.is_some()
        || flag_present(arguments, "--live-config")
    {
        return Err("ENGINE_ROUTE_CONFIG_VALIDATE_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let wire = super::native_config_read_only::discover_wire(project_root)?;
    let (_, snapshot) = config_snapshot(&wire)?;
    let result = json!({
        "ok": true,
        "config_hash": snapshot.config_hash,
        "warnings": snapshot.warnings,
    });
    Ok(envelope(
        "config.validate",
        "config.resolve",
        wire_input(&wire, "live-config-discovery-v1"),
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
    project_root: &Path,
) -> Result<Value, String> {
    let route = command
        .get(2)
        .map(String::as_str)
        .ok_or_else(|| "ENGINE_ROUTE_COMMAND_MISSING".to_owned())?;
    match route {
        "version" => Ok(route_version()),
        "status" => route_status(arguments, project_root),
        "config.resolve" => route_config_resolve(arguments),
        "config.show" => route_config_show(arguments, project_root),
        "config.validate" => route_config_validate(arguments, project_root),
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
        assert!(!supports(&command("config.explain")));
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
