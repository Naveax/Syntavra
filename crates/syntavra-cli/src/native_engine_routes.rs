#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::config_contract::{resolve_config_wire, snapshot_json};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;
const ALLOWED_SCHEDULER_STATES: &[&str] = &[
    "cancelled",
    "dead-letter",
    "failed",
    "queued",
    "running",
    "succeeded",
];

const STATIC_ROUTES: &[&str] = &[
    "config.resolve",
    "migration.plan",
    "pipeline.describe",
    "plugins.list",
    "scheduler.list",
    "scheduler.stats",
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

fn repeated_option_values(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut found = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        if item == flag {
            index += 1;
            found.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = item
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            found.push(value.to_owned());
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

fn canonical_input(profile: &str, format: &str, request: &[u8]) -> Value {
    json!({
        "profile": profile,
        "format": format,
        "bytes": request.len(),
        "sha256": sha256_hex(request),
    })
}

fn wire_input(wire: &[u8]) -> Value {
    canonical_input("explicit-config-wire-v1", "R6CFG1", wire)
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
    let result = json_value(&rendered, "ENGINE_ROUTE_TELEMETRY_RESULT_INVALID")?;
    let request = format!(r#"{{"format":"{output_format}","route":"telemetry.metrics"}}"#);
    Ok(envelope(
        "telemetry.metrics",
        "telemetry.metrics",
        canonical_input(
            "process-local-empty-metrics-v1",
            "canonical-output-format",
            request.as_bytes(),
        ),
        result,
    ))
}

fn normalize_lexical(path: &Path) -> Result<PathBuf, String> {
    let mut prefix = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(value) => prefix.push(value.as_os_str()),
            Component::RootDir => prefix.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                if !prefix.pop() {
                    return Err("MIGRATION_PLAN_DATABASE_PATH_ESCAPE".to_owned());
                }
            }
            Component::Normal(value) => prefix.push(value),
        }
    }
    Ok(prefix)
}

fn migration_logical_database(project_root: &Path, raw: &str) -> Result<String, String> {
    let value = raw.trim();
    if value.is_empty() || value.contains('\0') || value.len() > 4096 {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    let root = normalize_lexical(project_root)?;
    let candidate = Path::new(value);
    let selected = normalize_lexical(if candidate.is_absolute() {
        candidate
    } else {
        &root.join(candidate)
    })?;
    let relative = selected
        .strip_prefix(&root)
        .map_err(|_| "MIGRATION_PLAN_DATABASE_PATH_ESCAPE".to_owned())?;
    if relative.as_os_str().is_empty() {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    let logical = relative
        .components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/");
    if logical.is_empty() {
        return Err("MIGRATION_PLAN_DATABASE_PATH_INVALID".to_owned());
    }
    Ok(logical)
}

fn route_migration_plan(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    let database = option_value(arguments, "--migration-database")?
        .ok_or_else(|| "ENGINE_ROUTE_MIGRATION_DATABASE_REQUIRED_R24".to_owned())?;
    let logical = migration_logical_database(project_root, &database)?;
    let project = project_root.to_string_lossy();
    let result = json_value(
        &super::migration_plan_read_only_contract::migration_plan_json(&project, &logical)?,
        "ENGINE_ROUTE_MIGRATION_RESULT_INVALID",
    )?;
    let encoded = serde_json::to_string(&logical)
        .map_err(|_| "ENGINE_ROUTE_MIGRATION_REQUEST_INVALID".to_owned())?;
    let request = format!(r#"{{"database":{encoded},"route":"migration.plan"}}"#);
    Ok(envelope(
        "migration.plan",
        "migration.plan",
        canonical_input(
            "project-bound-quiescent-migration-sqlite-v1",
            "canonical-project-relative-path",
            request.as_bytes(),
        ),
        result,
    ))
}

fn scheduler_states(arguments: &[String]) -> Result<Vec<String>, String> {
    let mut states = BTreeSet::new();
    for value in repeated_option_values(arguments, "--scheduler-state")? {
        let state = value.trim().to_ascii_lowercase();
        if !ALLOWED_SCHEDULER_STATES.contains(&state.as_str()) {
            return Err("SCHEDULER_READ_ONLY_STATE_INVALID".to_owned());
        }
        states.insert(state);
    }
    if states.len() > 16 {
        return Err("SCHEDULER_READ_ONLY_TOO_MANY_STATES".to_owned());
    }
    Ok(states.into_iter().collect())
}

fn scheduler_limit(arguments: &[String]) -> Result<usize, String> {
    let value = option_value(arguments, "--scheduler-limit")?;
    let parsed = value.map_or(Ok(100_i64), |item| {
        item.parse::<i64>()
            .map_err(|_| "SCHEDULER_READ_ONLY_LIMIT_INVALID".to_owned())
    })?;
    Ok(parsed.clamp(1, 1000) as usize)
}

fn route_scheduler(
    route: &str,
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    let states = scheduler_states(arguments)?;
    let explicit_limit = option_value(arguments, "--scheduler-limit")?.is_some();
    if route == "scheduler.stats" && (!states.is_empty() || explicit_limit) {
        return Err("ENGINE_ROUTE_SCHEDULER_STATS_FILTER_UNSUPPORTED_R24".to_owned());
    }
    let limit = scheduler_limit(arguments)?;
    let state = state_root.to_string_lossy();
    let (result, request) = if route == "scheduler.stats" {
        let rendered = super::scheduler_read_only_contract::scheduler_stats_json(&state)?;
        (
            json_value(&rendered, "ENGINE_ROUTE_SCHEDULER_RESULT_INVALID")?,
            r#"{"route":"scheduler.stats"}"#.to_owned(),
        )
    } else {
        let encoded_states = serde_json::to_vec(&states)
            .map_err(|_| "ENGINE_ROUTE_SCHEDULER_STATES_INVALID".to_owned())?;
        let rendered = super::scheduler_read_only_contract::scheduler_list_json(
            &state,
            limit,
            &encoded_states,
        )?;
        let states_json = serde_json::to_string(&states)
            .map_err(|_| "ENGINE_ROUTE_SCHEDULER_STATES_INVALID".to_owned())?;
        (
            json_value(&rendered, "ENGINE_ROUTE_SCHEDULER_RESULT_INVALID")?,
            format!(
                r#"{{"limit":{limit},"route":"scheduler.list","states":{states_json}}}"#
            ),
        )
    };
    Ok(envelope(
        route,
        route,
        canonical_input(
            "selected-state-root-quiescent-scheduler-sqlite-v1",
            "implicit-database-path+canonical-json",
            request.as_bytes(),
        ),
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
        "config.resolve" => route_config_resolve(arguments),
        "migration.plan" => route_migration_plan(arguments, project_root),
        "pipeline.describe" | "plugins.list" => route_static(route),
        "scheduler.list" | "scheduler.stats" => {
            let state_root = option_value(arguments, "--state-root")?
                .map(PathBuf::from)
                .unwrap_or_else(|| project_root.join(".syntavra").join("pre-release"));
            route_scheduler(route, arguments, &state_root)
        }
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
        assert!(supports(&command("migration.plan")));
        assert!(supports(&command("scheduler.list")));
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
