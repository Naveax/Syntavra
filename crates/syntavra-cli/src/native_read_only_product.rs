#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use serde_json::{json, Value};

use super::migration_plan_read_only_contract::migration_plan_json;
use super::read_only_cli_contract::result_json as static_result_json;
use super::scheduler_read_only_contract::{scheduler_list_json, scheduler_stats_json};
use super::telemetry_metrics_contract::telemetry_metrics_json;

const VERSION: &str = "0.0.1";

fn parse_json(value: &str, code: &str) -> Result<Value, String> {
    serde_json::from_str(value).map_err(|_| code.to_owned())
}

fn argument_value(arguments: &[String], flag: &str) -> Option<String> {
    arguments
        .iter()
        .position(|value| value == flag)
        .and_then(|index| arguments.get(index + 1))
        .cloned()
        .or_else(|| {
            arguments
                .iter()
                .find_map(|value| value.strip_prefix(&format!("{flag}=")).map(str::to_owned))
        })
}

fn positional_after(arguments: &[String], command: &str, action: &str) -> Option<String> {
    arguments
        .windows(2)
        .position(|window| window[0] == command && window[1] == action)
        .and_then(|index| arguments.get(index + 2))
        .filter(|value| !value.starts_with('-'))
        .cloned()
}

fn pyproject_version(path: &Path) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    text.lines().find_map(|line| {
        let trimmed = line.trim();
        let value = trimmed.strip_prefix("version")?.trim_start();
        let value = value.strip_prefix('=')?.trim();
        value
            .strip_prefix('"')?
            .strip_suffix('"')
            .map(str::to_owned)
    })
}

fn json_version(path: &Path) -> Option<String> {
    let value: Value = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    value.get("version")?.as_str().map(str::to_owned)
}

fn check(name: &str, actual: Option<String>) -> Value {
    json!({
        "name": name,
        "passed": actual.as_deref() == Some(VERSION),
        "actual": actual,
        "expected": VERSION,
    })
}

fn version(project_root: &Path) -> Value {
    let mut checks = vec![
        check(
            "VERSION",
            fs::read_to_string(project_root.join("VERSION"))
                .ok()
                .map(|value| value.trim().to_owned()),
        ),
        check(
            "pyproject",
            pyproject_version(&project_root.join("pyproject.toml")),
        ),
    ];
    let typescript = project_root.join("sdk/typescript/package.json");
    if typescript.is_file() {
        checks.push(check("typescript", json_version(&typescript)));
    }
    let codemeta = project_root.join("codemeta.json");
    if codemeta.is_file() {
        checks.push(check("codemeta", json_version(&codemeta)));
    }
    let ok = checks
        .iter()
        .all(|row| row.get("passed").and_then(Value::as_bool) == Some(true));
    json!({
        "identity": {
            "version": VERSION,
            "channel": "pre-release",
            "stability": "pre-alpha",
            "version_locked": true,
            "public_superiority_claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "infinite_context_claim": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
        },
        "repository": {
            "ok": ok,
            "identity": {
                "version": VERSION,
                "channel": "pre-release",
                "stability": "pre-alpha",
                "version_locked": true,
                "public_superiority_claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
                "infinite_context_claim": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
            },
            "checks": checks,
        },
    })
}

fn scheduler_states(arguments: &[String]) -> Result<Vec<u8>, String> {
    let mut states = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == "--state" {
            let value = arguments
                .get(index + 1)
                .ok_or_else(|| "SCHEDULER_STATE_VALUE_MISSING".to_owned())?;
            states.push(Value::String(value.clone()));
            index += 2;
        } else {
            index += 1;
        }
    }
    serde_json::to_vec(&states).map_err(|_| "SCHEDULER_STATES_JSON_FAILED".to_owned())
}

fn scheduler_limit(arguments: &[String]) -> Result<usize, String> {
    argument_value(arguments, "--limit").map_or(Ok(100), |value| {
        value
            .parse::<usize>()
            .map_err(|_| "SCHEDULER_LIMIT_INVALID".to_owned())
    })
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [value] if value == "version")
        || matches!(command, [group, action]
            if (group == "migrate" && action == "plan")
                || (group == "pipeline" && action == "describe")
                || (group == "plugins" && action == "list")
                || (group == "scheduler" && matches!(action.as_str(), "list" | "stats"))
                || (group == "telemetry" && action == "metrics"))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    match command {
        [value] if value == "version" => Ok(version(project_root)),
        [group, action] if group == "pipeline" && action == "describe" => parse_json(
            &static_result_json("pipeline.describe")?,
            "PIPELINE_JSON_INVALID",
        ),
        [group, action] if group == "plugins" && action == "list" => {
            parse_json(&static_result_json("plugins.list")?, "PLUGINS_JSON_INVALID")
        }
        [group, action] if group == "migrate" && action == "plan" => {
            let database = positional_after(arguments, "migrate", "plan")
                .ok_or_else(|| "MIGRATION_DATABASE_MISSING".to_owned())?;
            let project = project_root.to_string_lossy();
            parse_json(
                &migration_plan_json(&project, &database)?,
                "MIGRATION_PLAN_JSON_INVALID",
            )
        }
        [group, action] if group == "scheduler" && action == "stats" => {
            let state = state_root.to_string_lossy();
            parse_json(
                &scheduler_stats_json(&state)?,
                "SCHEDULER_STATS_JSON_INVALID",
            )
        }
        [group, action] if group == "scheduler" && action == "list" => {
            let state = state_root.to_string_lossy();
            let states = scheduler_states(arguments)?;
            parse_json(
                &scheduler_list_json(&state, scheduler_limit(arguments)?, &states)?,
                "SCHEDULER_LIST_JSON_INVALID",
            )
        }
        [group, action] if group == "telemetry" && action == "metrics" => {
            let format = if arguments.iter().any(|value| value == "--prometheus") {
                "prometheus"
            } else {
                "json"
            };
            let envelope = parse_json(
                &telemetry_metrics_json(format)?,
                "TELEMETRY_METRICS_JSON_INVALID",
            )?;
            if format == "prometheus" {
                Ok(envelope)
            } else {
                envelope
                    .get("metrics")
                    .cloned()
                    .ok_or_else(|| "TELEMETRY_METRICS_MISSING".to_owned())
            }
        }
        _ => Err("RUST_READ_ONLY_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{supports, version};
    use std::path::Path;

    #[test]
    fn exposes_all_catalogued_read_only_paths() {
        for command in [
            vec!["version"],
            vec!["migrate", "plan"],
            vec!["pipeline", "describe"],
            vec!["plugins", "list"],
            vec!["scheduler", "list"],
            vec!["scheduler", "stats"],
            vec!["telemetry", "metrics"],
        ] {
            let command = command.into_iter().map(str::to_owned).collect::<Vec<_>>();
            assert!(supports(&command), "missing {command:?}");
        }
    }

    #[test]
    fn version_identity_remains_locked() {
        let value = version(Path::new("."));
        assert_eq!(value["identity"]["version"], "0.0.1");
        assert_eq!(value["identity"]["version_locked"], true);
    }
}
