#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::config_contract::{default_config_wire, resolve_config_wire, snapshot_json};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;
const MAX_CONFIG_WIRE_BYTES: usize = 256 * 1024;
const MAX_EXPLAIN_PATH_BYTES: usize = 512;
const ALLOWED_SCHEDULER_STATES: &[&str] = &[
    "cancelled",
    "dead-letter",
    "failed",
    "queued",
    "running",
    "succeeded",
];

const ROUTES: &[&str] = &[
    "config.explain",
    "config.resolve",
    "config.show",
    "config.validate",
    "migration.plan",
    "pipeline.describe",
    "plugins.list",
    "scheduler.list",
    "scheduler.stats",
    "state.layout",
    "status",
    "telemetry.metrics",
    "version",
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

fn decode_hex(value: &str, maximum_bytes: usize, code: &str) -> Result<Vec<u8>, String> {
    if value.len() > maximum_bytes.saturating_mul(2) || value.len() % 2 != 0 {
        return Err(code.to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = hex_nibble(pair[0]).ok_or_else(|| code.to_owned())?;
        let low = hex_nibble(pair[1]).ok_or_else(|| code.to_owned())?;
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

fn bytes_hex(value: &[u8]) -> String {
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
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

fn wire_input(profile: &str, wire: &[u8]) -> Value {
    canonical_input(profile, "R6CFG1", wire)
}

fn json_value(rendered: &str, code: &str) -> Result<Value, String> {
    serde_json::from_str(rendered).map_err(|_| code.to_owned())
}

fn unsupported_route_inputs(arguments: &[String], allowed: &[&str]) -> Result<bool, String> {
    let option_flags = [
        "--config-wire-hex",
        "--session-override-json-hex",
        "--task-override-json-hex",
        "--receipt-wire-hex",
        "--database-path",
        "--explain-path",
        "--scheduler-limit",
        "--migration-database",
    ];
    for flag in option_flags {
        if !allowed.contains(&flag) && option_value(arguments, flag)?.is_some() {
            return Ok(true);
        }
    }
    if !allowed.contains(&"--scheduler-state")
        && !repeated_option_values(arguments, "--scheduler-state")?.is_empty()
    {
        return Ok(true);
    }
    let boolean_flags = ["--live-config", "--telemetry-prometheus"];
    Ok(boolean_flags
        .iter()
        .any(|flag| !allowed.contains(flag) && flag_present(arguments, flag)))
}

fn rust_runtime(arguments: &[&str]) -> Result<Value, String> {
    let owned = arguments
        .iter()
        .map(|value| (*value).to_owned())
        .collect::<Vec<_>>();
    super::native_rust_subprocess::execute_json(&owned)
}

fn route_version(arguments: &[String]) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &[])? {
        return Err("ENGINE_ROUTE_VERSION_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let result = rust_runtime(&["version"])?;
    Ok(envelope("version", "version", no_input(), result))
}

fn route_config_resolve(arguments: &[String]) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &["--config-wire-hex"])? {
        return Err("ENGINE_ROUTE_CONFIG_RESOLVE_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let encoded = option_value(arguments, "--config-wire-hex")?
        .ok_or_else(|| "ENGINE_ROUTE_INPUT_REQUIRED_R14".to_owned())?;
    let wire = decode_hex(
        &encoded,
        MAX_CONFIG_WIRE_BYTES,
        "ENGINE_ROUTE_CONFIG_WIRE_HEX_INVALID",
    )?;
    let snapshot = resolve_config_wire(&wire)?;
    let result = json_value(
        &snapshot_json(&snapshot)?,
        "ENGINE_ROUTE_CONFIG_JSON_INVALID",
    )?;
    Ok(envelope(
        "config.resolve",
        "config.resolve",
        wire_input("explicit-config-wire-v1", &wire),
        result,
    ))
}

fn live_wire_at(
    project_root: &Path,
    arguments: &[String],
    allow_overrides: bool,
) -> Result<(Vec<u8>, &'static str), String> {
    let session = option_value(arguments, "--session-override-json-hex")?;
    let task = option_value(arguments, "--task-override-json-hex")?;
    if !allow_overrides && (session.is_some() || task.is_some()) {
        return Err("ENGINE_ROUTE_LIVE_OVERRIDE_UNSUPPORTED_R24".to_owned());
    }
    let profile = if session.is_some() || task.is_some() {
        "live-config-session-task-v1"
    } else {
        "live-config-discovery-v1"
    };
    super::native_live_config::discover_wire(project_root, session.as_deref(), task.as_deref())
        .map(|wire| (wire, profile))
}

fn route_config_show(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &[])? {
        return Err("ENGINE_ROUTE_CONFIG_SHOW_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let (wire, profile) = live_wire_at(project_root, arguments, false)?;
    let encoded = bytes_hex(&wire);
    let result = rust_runtime(&["config", "show", &encoded])?;
    Ok(envelope(
        "config.show",
        "config.show",
        wire_input(profile, &wire),
        result,
    ))
}

fn validate_explain_path(value: &str) -> Result<(), String> {
    let encoded = value.as_bytes();
    if encoded.is_empty()
        || encoded.len() > MAX_EXPLAIN_PATH_BYTES
        || value.chars().any(char::is_control)
        || value.split('.').any(str::is_empty)
    {
        return Err("ENGINE_ROUTE_CONFIG_EXPLAIN_PATH_INVALID_R24".to_owned());
    }
    Ok(())
}

fn route_config_explain(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &["--explain-path"])? {
        return Err("ENGINE_ROUTE_CONFIG_EXPLAIN_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let path = option_value(arguments, "--explain-path")?
        .ok_or_else(|| "ENGINE_ROUTE_CONFIG_EXPLAIN_PATH_INVALID_R24".to_owned())?;
    validate_explain_path(&path)?;
    let (wire, profile) = live_wire_at(project_root, arguments, false)?;
    let wire_hex = bytes_hex(&wire);
    let path_hex = bytes_hex(path.as_bytes());
    let result = rust_runtime(&["config", "explain", &wire_hex, &path_hex])?;
    let mut input = wire_input(profile, &wire);
    let object = input
        .as_object_mut()
        .ok_or_else(|| "ENGINE_ROUTE_CONFIG_EXPLAIN_INPUT_INVALID".to_owned())?;
    object.insert("path".to_owned(), Value::String(path.clone()));
    object.insert("path_bytes".to_owned(), Value::Number(path.len().into()));
    object.insert(
        "path_sha256".to_owned(),
        Value::String(sha256_hex(path.as_bytes())),
    );
    Ok(envelope("config.explain", "config.explain", input, result))
}

fn route_config_validate(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &[])? {
        return Err("ENGINE_ROUTE_CONFIG_VALIDATE_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let (wire, profile) = live_wire_at(project_root, arguments, false)?;
    let encoded = bytes_hex(&wire);
    let snapshot = rust_runtime(&["config", "resolve", &encoded])?;
    let result = json!({
        "ok": true,
        "config_hash": snapshot
            .get("config_hash")
            .cloned()
            .ok_or_else(|| "CONFIG_VALIDATE_HASH_MISSING".to_owned())?,
        "warnings": snapshot
            .get("warnings")
            .cloned()
            .ok_or_else(|| "CONFIG_VALIDATE_WARNINGS_MISSING".to_owned())?,
    });
    Ok(envelope(
        "config.validate",
        "config.resolve",
        wire_input(profile, &wire),
        result,
    ))
}

fn route_status(arguments: &[String], project_root: &Path) -> Result<Value, String> {
    if unsupported_route_inputs(
        arguments,
        &[
            "--config-wire-hex",
            "--live-config",
            "--session-override-json-hex",
            "--task-override-json-hex",
        ],
    )? {
        return Err("ENGINE_ROUTE_STATUS_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let explicit = option_value(arguments, "--config-wire-hex")?;
    let live = flag_present(arguments, "--live-config");
    let session = option_value(arguments, "--session-override-json-hex")?;
    let task = option_value(arguments, "--task-override-json-hex")?;
    if explicit.is_some() && (live || session.is_some() || task.is_some()) {
        return Err("ENGINE_ROUTE_INPUT_CONFLICT_R16".to_owned());
    }
    if (session.is_some() || task.is_some()) && !live {
        return Err("ENGINE_ROUTE_OVERRIDE_REQUIRES_LIVE_CONFIG_R16".to_owned());
    }

    let (wire, profile, result) = if let Some(encoded) = explicit {
        let wire = decode_hex(
            &encoded,
            MAX_CONFIG_WIRE_BYTES,
            "ENGINE_ROUTE_CONFIG_WIRE_HEX_INVALID",
        )?;
        let result = rust_runtime(&["status", &bytes_hex(&wire)])?;
        (wire, "explicit-config-wire-v1", result)
    } else if live {
        let (wire, profile) = live_wire_at(project_root, arguments, true)?;
        let result = rust_runtime(&["status", &bytes_hex(&wire)])?;
        (wire, profile, result)
    } else {
        let wire = default_config_wire().to_vec();
        let result = rust_runtime(&["status"])?;
        (wire, "default-config-only", result)
    };
    Ok(envelope(
        "status",
        "status",
        wire_input(profile, &wire),
        result,
    ))
}

fn route_static(command: &str, arguments: &[String]) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &[])? {
        return Err("ENGINE_ROUTE_STATIC_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let rendered = super::read_only_cli_contract::result_json(command)?;
    let result = json_value(&rendered, "ENGINE_ROUTE_STATIC_RESULT_INVALID")?;
    Ok(envelope(command, command, no_input(), result))
}

fn route_state_layout(arguments: &[String]) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &[])? {
        return Err("ENGINE_ROUTE_STATE_LAYOUT_INPUT_UNSUPPORTED_R24".to_owned());
    }
    let result = rust_runtime(&["state", "layout"])?;
    Ok(envelope("state.layout", "state.layout", no_input(), result))
}

fn route_telemetry(arguments: &[String]) -> Result<Value, String> {
    if unsupported_route_inputs(arguments, &["--telemetry-prometheus"])? {
        return Err("ENGINE_ROUTE_TELEMETRY_INPUT_UNSUPPORTED_R24".to_owned());
    }
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
    let selected_path = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    let selected = normalize_lexical(&selected_path)?;
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
    if unsupported_route_inputs(arguments, &["--migration-database"])? {
        return Err("ENGINE_ROUTE_MIGRATION_INPUT_UNSUPPORTED_R24".to_owned());
    }
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

fn route_scheduler(route: &str, arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let allowed = if route == "scheduler.list" {
        &["--scheduler-state", "--scheduler-limit"][..]
    } else {
        &[][..]
    };
    if unsupported_route_inputs(arguments, allowed)? {
        return Err("ENGINE_ROUTE_SCHEDULER_INPUT_UNSUPPORTED_R24".to_owned());
    }
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
            format!(r#"{{"limit":{limit},"route":"scheduler.list","states":{states_json}}}"#),
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
    state_root: &Path,
) -> Result<Value, String> {
    let route = command
        .get(2)
        .map(String::as_str)
        .ok_or_else(|| "ENGINE_ROUTE_COMMAND_MISSING".to_owned())?;
    match route {
        "version" => route_version(arguments),
        "status" => route_status(arguments, project_root),
        "config.resolve" => route_config_resolve(arguments),
        "config.show" => route_config_show(arguments, project_root),
        "config.explain" => route_config_explain(arguments, project_root),
        "config.validate" => route_config_validate(arguments, project_root),
        "migration.plan" => route_migration_plan(arguments, project_root),
        "pipeline.describe" | "plugins.list" => route_static(route, arguments),
        "scheduler.list" | "scheduler.stats" => route_scheduler(route, arguments, state_root),
        "state.layout" => route_state_layout(arguments),
        "telemetry.metrics" => route_telemetry(arguments),
        _ => Err("ENGINE_ROUTE_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{supports, validate_explain_path};

    fn command(route: &str) -> Vec<String> {
        ["engine", "route", route]
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    #[test]
    fn recognizes_complete_non_state_route_family() {
        assert!(supports(&command("version")));
        assert!(supports(&command("status")));
        assert!(supports(&command("config.explain")));
        assert!(supports(&command("migration.plan")));
        assert!(supports(&command("scheduler.list")));
    }

    #[test]
    fn explain_path_validation_is_fail_closed() {
        assert!(validate_explain_path("runtime.profile").is_ok());
        assert!(validate_explain_path("runtime..profile").is_err());
        assert!(validate_explain_path("").is_err());
    }
}
