#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if matches!(root.as_str(), "setup" | "repair"))
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn load_config(path: &Path) -> Value {
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(_) => return Value::Object(Map::new()),
    };
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(value)) => Value::Object(value),
        Ok(_) | Err(_) => json!({"invalid": true}),
    }
}

fn doctor(project_root: &Path, state_root: &Path) -> Result<Value, String> {
    super::native_operator_lifecycle::execute(&["doctor".to_owned()], project_root, state_root)
        .map(|decision| decision.value)
}

fn finding_rows(value: &Value) -> Vec<Value> {
    value
        .get("issues")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .chain(
            value
                .get("warnings")
                .and_then(Value::as_array)
                .into_iter()
                .flatten(),
        )
        .cloned()
        .collect()
}

fn finding_codes(rows: &[Value]) -> Vec<&str> {
    rows.iter()
        .filter_map(|row| row.get("code").and_then(Value::as_str))
        .collect()
}

fn configured_hosts(config: &Value) -> Vec<String> {
    config
        .get("hosts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

fn repair(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    let apply = has_flag(arguments, "--apply");
    let diagnosis = doctor(project_root, state_root)?;
    let findings = finding_rows(&diagnosis);
    let actions = findings
        .iter()
        .filter_map(|row| row.get("repair").and_then(Value::as_str))
        .map(str::to_owned)
        .collect::<Vec<_>>();

    if apply {
        let config = load_config(&state_root.join("config.json"));
        let profile = config
            .get("mcp_profile")
            .and_then(Value::as_str)
            .unwrap_or("minimal");
        let codes = finding_codes(&findings);
        if codes.contains(&"not-installed") {
            let install_arguments = vec![
                "install".to_owned(),
                "--apply".to_owned(),
                "--mcp-profile".to_owned(),
                profile.to_owned(),
            ];
            let _ = super::native_install::execute(&install_arguments, project_root, state_root)?;
        } else if codes.contains(&"product-bundle-incomplete") {
            let _ = super::native_install::repair_bundle(project_root, state_root, profile)?;
        }
        if codes.contains(&"host-integration-verification-failed") {
            for host in configured_hosts(&config) {
                let _ = super::native_install::reapply_host(&host, project_root, state_root)?;
            }
        }
    }

    let final_value = if apply {
        doctor(project_root, state_root)?
    } else {
        diagnosis
    };
    let remaining = finding_rows(&final_value);
    Ok(Decision {
        exit_code: 0,
        value: json!({
            "ok": final_value.get("ok").and_then(Value::as_bool) == Some(true),
            "apply": apply,
            "actions": actions,
            "remaining": remaining,
        }),
    })
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    match command {
        [root] if root == "repair" => repair(arguments, project_root, state_root),
        [root] if root == "setup" && has_flag(arguments, "--repair") => {
            let mut decision = repair(arguments, project_root, state_root)?;
            decision.exit_code = if decision.value["ok"].as_bool() == Some(true) {
                0
            } else {
                2
            };
            Ok(decision)
        }
        [root] if root == "setup" => {
            let value = super::native_install::execute(arguments, project_root, state_root)?;
            Ok(Decision {
                exit_code: if value["ok"].as_bool() == Some(true) {
                    0
                } else {
                    2
                },
                value,
            })
        }
        _ => Err("SETUP_REPAIR_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_setup_and_repair() {
        assert!(supports(&["setup".to_owned()]));
        assert!(supports(&["repair".to_owned()]));
        assert!(!supports(&["install".to_owned()]));
    }
}
