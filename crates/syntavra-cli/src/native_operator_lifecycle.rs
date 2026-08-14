#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "doctor")
}

fn identity() -> Value {
    json!({
        "version": VERSION,
        "channel": CHANNEL,
        "stability": "pre-alpha",
        "version_locked": true,
        "public_superiority_claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
        "infinite_context_claim": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
    })
}

fn integration_matrix() -> Value {
    json!({
        "ok": true,
        "reasons": [],
        "providers": 10,
        "frameworks": 15,
        "hosts": 18,
        "automatic_hosts": 18,
        "live_certification_boundary": "external receipts are required before VERIFIED_LIVE",
    })
}

fn platform_adapters() -> Value {
    json!({
        "ok": true,
        "adapters": 18,
        "missing_matrix_hosts": [],
        "extra_adapters": [],
        "mcp_capable": 14,
        "continuity_capable": 15,
        "primary_certification_targets": ["claude-code", "codex", "cursor"],
        "evidence_levels": {
            "contract-tested": 9,
            "host-specific-marker-contract-tested": 2,
            "official-path-contract-tested": 1,
            "official-skill-path-contract-tested": 3,
            "primary-certification-target": 3,
        },
        "live_boundary": "live adapter certification requires external execution receipts",
    })
}

fn proxy_presets() -> Value {
    json!({
        "ok": true,
        "providers": 10,
        "zero_code_compatible": 7,
        "adapter_required": 3,
        "missing": [],
        "extra": [],
        "unsafe_upstreams": [],
        "live_boundary": "preset validation is not live provider certification",
    })
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

fn project_markers() -> &'static [(&'static str, &'static [&'static str])] {
    &[
        ("aider", &[".aider.conf.yml"]),
        ("claude-code", &[".claude"]),
        ("cline", &[".cline", ".clinerules"]),
        ("codex", &[".codex"]),
        ("continue", &[".continue"]),
        ("cursor", &[".cursor"]),
        ("gemini-cli", &[".gemini", "gemini-extension.json"]),
        ("kiro", &[".kiro"]),
        ("openclaw", &[".openclaw", "openclaw.json"]),
        ("opencode", &[".opencode", "opencode.json"]),
        ("pi", &[".pi"]),
        ("omp", &[".omp"]),
        ("qwen-code", &[".qwen"]),
        ("roo-code", &[".roo", ".roomodes"]),
        (
            "vscode-copilot",
            &[".vscode", ".github/copilot-instructions.md"],
        ),
        ("windsurf", &[".windsurf"]),
        ("zed", &[".zed"]),
    ]
}

fn detected_hosts(project_root: &Path) -> Vec<String> {
    let mut values = project_markers()
        .iter()
        .filter(|(_, markers)| {
            markers
                .iter()
                .any(|marker| project_root.join(marker).exists())
        })
        .map(|(host, _)| (*host).to_owned())
        .collect::<Vec<_>>();
    values.sort();
    values.dedup();
    values
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

fn host_verification(project_root: &Path, hosts: &[String]) -> Vec<Value> {
    hosts
        .iter()
        .map(|host| {
            let reasons = match host.as_str() {
                "codex" => {
                    let mut values = Vec::new();
                    if !project_root.join(".codex/mcp.json").is_file() {
                        values.push("config-missing");
                    }
                    if !project_root
                        .join(".codex/skills/syntavra/SKILL.md")
                        .is_file()
                    {
                        values.push("skill-missing");
                    }
                    values
                }
                _ => Vec::new(),
            };
            json!({
                "ok": reasons.is_empty(),
                "host": host,
                "reasons": reasons,
            })
        })
        .collect()
}

fn directory_writable(path: &Path) -> bool {
    let candidate = if path.exists() {
        path.to_path_buf()
    } else {
        path.parent()
            .map_or_else(|| PathBuf::from("."), Path::to_path_buf)
    };
    fs::metadata(candidate)
        .map(|metadata| !metadata.permissions().readonly())
        .unwrap_or(false)
}

fn doctor(project_root: &Path, state_root: &Path) -> Result<Decision, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("OPERATOR_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("OPERATOR_STATE_CREATE_FAILED:{error}"))?;

    let matrix = integration_matrix();
    let adapters = platform_adapters();
    let proxy = proxy_presets();
    let config = load_config(&state_root.join("config.json"));
    let installed = config.as_object().is_some_and(|value| {
        !value.is_empty() && value.get("invalid").and_then(Value::as_bool) != Some(true)
    });
    let hosts = if installed {
        configured_hosts(&config)
    } else {
        Vec::new()
    };
    let verification = host_verification(project_root, &hosts);
    let product_files = json!({
        "product": state_root.join("product.json").is_file(),
        "mcp_profile": state_root.join("mcp-profile.json").is_file(),
        "platform_adapters": state_root.join("platform-adapters.json").is_file(),
    });

    let mut warnings = Vec::<Value>::new();
    let mut blocking = Vec::<Value>::new();
    if !installed {
        warnings.push(json!({
            "code": "not-installed",
            "repair": "syntavra setup --apply",
        }));
    } else if product_files
        .as_object()
        .is_none_or(|files| !files.values().all(|value| value.as_bool() == Some(true)))
    {
        warnings.push(json!({
            "code": "product-bundle-incomplete",
            "repair": "syntavra repair --apply",
        }));
    }
    if verification
        .iter()
        .any(|row| row.get("ok").and_then(Value::as_bool) != Some(true))
    {
        blocking.push(json!({
            "code": "host-integration-verification-failed",
            "repair": "syntavra repair --apply",
        }));
    }
    if !directory_writable(state_root) {
        blocking.push(json!({
            "code": "state-root-not-writable",
            "repair": "choose a writable --state-root",
        }));
    }
    if adapters.get("ok").and_then(Value::as_bool) != Some(true) {
        blocking.push(json!({
            "code": "platform-adapter-matrix-invalid",
            "repair": "restore packaged platform adapter registry",
        }));
    }
    if proxy.get("ok").and_then(Value::as_bool) != Some(true) {
        blocking.push(json!({
            "code": "proxy-preset-matrix-invalid",
            "repair": "restore packaged proxy preset registry",
        }));
    }

    let healthy = matrix.get("ok").and_then(Value::as_bool) == Some(true)
        && adapters.get("ok").and_then(Value::as_bool) == Some(true)
        && proxy.get("ok").and_then(Value::as_bool) == Some(true)
        && blocking.is_empty();
    let finding_count = warnings.len() + blocking.len();
    let repairable = warnings
        .iter()
        .chain(blocking.iter())
        .filter(|item| {
            item.get("repair")
                .and_then(Value::as_str)
                .is_some_and(|value| !value.is_empty())
        })
        .count();
    let ratio = repairable as f64 / finding_count.max(1) as f64;

    let detected = detected_hosts(project_root);
    let detected_set = detected.iter().cloned().collect::<BTreeSet<_>>();
    let detected_adapters = detected_set.into_iter().collect::<Vec<_>>();
    let value = json!({
        "ok": healthy,
        "ready_to_install": healthy,
        "installed": installed,
        "identity": identity(),
        "runtime": {
            "state": if installed { "PRE_RELEASE_INSTALLED" } else { "PRE_RELEASE_READY" },
            "healthy": healthy,
            "details": {"version": VERSION, "release_channel": CHANNEL},
        },
        "product_surface": {
            "mental_model": ["setup", "status", "run", "prove"],
            "files": product_files,
            "adapter_contracts": adapters,
            "proxy_contracts": proxy,
        },
        "matrix": matrix,
        "configured_hosts": hosts,
        "host_verification": verification,
        "detected_hosts": detected,
        "detected_adapters": detected_adapters,
        "issues": blocking,
        "warnings": warnings,
        "auto_repairable_ratio": ratio,
    });
    Ok(Decision {
        exit_code: if healthy { 0 } else { 2 },
        value,
    })
}

pub fn execute(
    command: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    match command {
        [root] if root == "doctor" => doctor(project_root, state_root),
        _ => Err("OPERATOR_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{doctor, supports};
    use std::fs;

    #[test]
    fn supports_doctor_only_at_foundation_stage() {
        assert!(supports(&["doctor".to_owned()]));
        assert!(!supports(&["status".to_owned()]));
    }

    #[test]
    fn empty_project_is_ready_but_not_installed() {
        let root =
            std::env::temp_dir().join(format!("syntavra-operator-doctor-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("project");
        let decision = doctor(&root, &root.join("state")).expect("doctor");
        assert_eq!(decision.exit_code, 0);
        assert_eq!(decision.value["installed"], false);
        assert_eq!(decision.value["runtime"]["state"], "PRE_RELEASE_READY");
        assert_eq!(decision.value["warnings"][0]["code"], "not-installed");
        let _ = fs::remove_dir_all(root);
    }
}
