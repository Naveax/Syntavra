#![forbid(unsafe_code)]

use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

#[derive(Clone, Copy)]
enum FeatureToggle {
    Enabled,
    Disabled,
}

impl FeatureToggle {
    const fn enabled(self) -> bool {
        matches!(self, Self::Enabled)
    }
}

#[derive(Clone, Copy)]
struct Mode {
    name: &'static str,
    description: &'static str,
    output_budget_bytes: u64,
    context_budget_tokens: u64,
    schema_profile: &'static str,
    rewrite_commands: FeatureToggle,
    cache_optimize: FeatureToggle,
    memory_extract: FeatureToggle,
    auto_delegate: FeatureToggle,
    style: &'static str,
}

const MODES: [Mode; 6] = [
    Mode {
        name: "full",
        description: "Balanced default with every exact-preserving optimizer enabled.",
        output_budget_bytes: 24_000,
        context_budget_tokens: 8_000,
        schema_profile: "balanced",
        rewrite_commands: FeatureToggle::Enabled,
        cache_optimize: FeatureToggle::Enabled,
        memory_extract: FeatureToggle::Enabled,
        auto_delegate: FeatureToggle::Enabled,
        style: "normal",
    },
    Mode {
        name: "lite",
        description: "Conservative compression with minimal behavior change.",
        output_budget_bytes: 48_000,
        context_budget_tokens: 4_000,
        schema_profile: "balanced",
        rewrite_commands: FeatureToggle::Enabled,
        cache_optimize: FeatureToggle::Enabled,
        memory_extract: FeatureToggle::Disabled,
        auto_delegate: FeatureToggle::Disabled,
        style: "normal",
    },
    Mode {
        name: "ultra",
        description: "Codex-oriented maximum context economy with exact recovery handles.",
        output_budget_bytes: 8_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: FeatureToggle::Enabled,
        cache_optimize: FeatureToggle::Enabled,
        memory_extract: FeatureToggle::Enabled,
        auto_delegate: FeatureToggle::Enabled,
        style: "terse",
    },
    Mode {
        name: "commit",
        description: "Small diff/status surface for commit preparation.",
        output_budget_bytes: 12_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: FeatureToggle::Enabled,
        cache_optimize: FeatureToggle::Enabled,
        memory_extract: FeatureToggle::Disabled,
        auto_delegate: FeatureToggle::Disabled,
        style: "commit",
    },
    Mode {
        name: "review",
        description: "Evidence-rich code review with bounded output.",
        output_budget_bytes: 32_000,
        context_budget_tokens: 3_000,
        schema_profile: "balanced",
        rewrite_commands: FeatureToggle::Enabled,
        cache_optimize: FeatureToggle::Enabled,
        memory_extract: FeatureToggle::Enabled,
        auto_delegate: FeatureToggle::Enabled,
        style: "review",
    },
    Mode {
        name: "compress",
        description: "Output-only compression; routing and delegation disabled.",
        output_budget_bytes: 10_000,
        context_budget_tokens: 1_500,
        schema_profile: "minimal",
        rewrite_commands: FeatureToggle::Disabled,
        cache_optimize: FeatureToggle::Disabled,
        memory_extract: FeatureToggle::Disabled,
        auto_delegate: FeatureToggle::Disabled,
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
        "rewrite_commands": mode.rewrite_commands.enabled(),
        "cache_optimize": mode.cache_optimize.enabled(),
        "memory_extract": mode.memory_extract.enabled(),
        "auto_delegate": mode.auto_delegate.enabled(),
        "style": mode.style,
    })
}

fn normalize_mode(value: &str) -> Result<&'static str, String> {
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
        .ok_or_else(|| format!("unknown optimization mode: {value}"))
}

fn profile(value: &str) -> Result<Mode, String> {
    let canonical = normalize_mode(value)?;
    MODES
        .iter()
        .copied()
        .find(|mode| mode.name == canonical)
        .ok_or_else(|| format!("unknown optimization mode: {value}"))
}

fn mode_path(state_root: &Path) -> PathBuf {
    state_root.join("optimization-mode.json")
}

fn current(state_root: &Path) -> Result<Mode, String> {
    let path = mode_path(state_root);
    if !path.is_file() {
        return Ok(MODES[0]);
    }
    let value: Value = serde_json::from_slice(
        &fs::read(&path).map_err(|error| format!("MODE_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("MODE_JSON_INVALID:{error}"))?;
    let document = value
        .as_object()
        .ok_or_else(|| "MODE_DOCUMENT_INVALID".to_owned())?;
    profile(
        document
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("full"),
    )
}

fn manifest(state_root: &Path) -> Result<Value, String> {
    Ok(json!({
        "active": mode_json(current(state_root)?),
        "available": MODES.into_iter().map(mode_json).collect::<Vec<_>>(),
        "instant_switch": true,
    }))
}

fn argument_after_pair(arguments: &[String], group: &str, action: &str) -> Option<String> {
    arguments
        .windows(2)
        .position(|window| window[0] == group && window[1] == action)
        .and_then(|index| arguments.get(index + 2))
        .filter(|value| !value.starts_with('-'))
        .cloned()
}

fn argument_value(arguments: &[String], flag: &str, default: &str) -> String {
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
        .unwrap_or_else(|| default.to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("MODE_JSON_RENDER_FAILED:{error}"))
}

fn private_permissions(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(metadata) = fs::metadata(path) {
            let mut permissions = metadata.permissions();
            permissions.set_mode(0o600);
            let _ = fs::set_permissions(path, permissions);
        }
    }
    #[cfg(not(unix))]
    let _ = path;
}

fn atomic_write(path: &Path, payload: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "MODE_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| format!("MODE_PARENT_CREATE_FAILED:{error}"))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("mode"),
        std::process::id()
    ));
    let mut file =
        File::create(&temporary).map_err(|error| format!("MODE_TEMP_CREATE_FAILED:{error}"))?;
    file.write_all(payload)
        .map_err(|error| format!("MODE_TEMP_WRITE_FAILED:{error}"))?;
    file.sync_all()
        .map_err(|error| format!("MODE_TEMP_SYNC_FAILED:{error}"))?;
    private_permissions(&temporary);
    fs::rename(&temporary, path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("MODE_REPLACE_FAILED:{error}")
    })?;
    Ok(())
}

fn set(state_root: &Path, selected: &str, source: &str) -> Result<Value, String> {
    let selected = profile(selected)?;
    let updated_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "MODE_CLOCK_INVALID".to_owned())?
        .as_secs_f64();
    let mut body = json!({
        "mode": selected.name,
        "source": source,
        "updated_at": updated_at,
        "profile": mode_json(selected),
    });
    let receipt_hash = sha256_hex(&canonical_bytes(&body)?);
    body.as_object_mut()
        .ok_or_else(|| "MODE_BODY_INVALID".to_owned())?
        .insert("receipt_hash".to_owned(), Value::String(receipt_hash));
    let mut payload = canonical_bytes(&body)?;
    payload.push(b'\n');
    atomic_write(&mode_path(state_root), &payload)?;
    Ok(body)
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    match argument_after_pair(arguments, "run", "mode") {
        Some(selected) => set(
            state_root,
            &selected,
            &argument_value(arguments, "--source", "user"),
        ),
        None => manifest(state_root),
    }
}

#[cfg(test)]
mod tests {
    use super::{execute, normalize_mode};
    use std::fs;

    #[test]
    fn aliases_are_canonical() {
        assert_eq!(normalize_mode("balanced").expect("balanced"), "full");
        assert_eq!(normalize_mode("codex-ultra").expect("codex-ultra"), "ultra");
        assert_eq!(normalize_mode("off").expect("off"), "lite");
    }

    #[test]
    fn empty_state_exposes_full_manifest() {
        let root = std::env::temp_dir().join(format!("syntavra-mode-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let value = execute(&["run".to_owned(), "mode".to_owned()], &root).expect("manifest");
        assert_eq!(value["active"]["name"], "full");
        assert_eq!(value["available"].as_array().map(Vec::len), Some(6));
        assert_eq!(value["instant_switch"], true);
        let _ = fs::remove_dir_all(&root);
    }
}
