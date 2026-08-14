#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::io::{self, Read};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

#[path = "native_hook_evidence.rs"]
mod native_hook_evidence;
#[path = "native_hook_output.rs"]
mod native_hook_output;

const DESTRUCTIVE_PATTERNS: &[&str] = &[
    "rm -rf /",
    "rm -rf ~",
    "git reset --hard",
    "git clean -fdx",
    "git clean -xdf",
    "format c:",
    "del /s /q c:\\",
    "remove-item -recurse -force c:\\",
    "mkfs.",
    ":(){:|:&};:",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "hook")
}

fn phase(arguments: &[String]) -> Result<&str, String> {
    arguments
        .iter()
        .position(|value| value == "hook")
        .and_then(|index| arguments.get(index + 1))
        .map(String::as_str)
        .ok_or_else(|| "HOOK_PHASE_MISSING".to_owned())
}

fn payload_text(arguments: &[String]) -> Result<String, String> {
    for (index, value) in arguments.iter().enumerate() {
        if value == "--payload" {
            return arguments
                .get(index + 1)
                .cloned()
                .ok_or_else(|| "HOOK_PAYLOAD_MISSING".to_owned());
        }
        if let Some(payload) = value.strip_prefix("--payload=") {
            return Ok(payload.to_owned());
        }
    }
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .map_err(|error| format!("HOOK_STDIN_READ_FAILED:{error}"))?;
    Ok(input)
}

fn payload(arguments: &[String]) -> Result<Map<String, Value>, String> {
    let text = payload_text(arguments)?;
    let value = serde_json::from_str::<Value>(if text.trim().is_empty() { "{}" } else { &text })
        .map_err(|error| format!("HOOK_PAYLOAD_JSON_INVALID:{error}"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| "HOOK_PAYLOAD_OBJECT_REQUIRED".to_owned())
}

fn strings(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(rows)) => rows
            .iter()
            .map(|row| row.as_str().map_or_else(|| row.to_string(), str::to_owned))
            .collect(),
        Some(Value::String(text)) => text
            .split_whitespace()
            .filter(|row| !row.is_empty())
            .map(str::to_owned)
            .collect(),
        Some(row) => vec![row.to_string()],
        None => Vec::new(),
    }
}

fn pre_tool(payload: &Map<String, Value>, project_root: &Path) -> Result<Value, String> {
    let tool = payload
        .get("tool")
        .or_else(|| payload.get("name"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    let command = strings(payload.get("command").or_else(|| payload.get("argv")));
    if !matches!(
        tool,
        "shell" | "bash" | "exec" | "command" | "terminal" | "powershell"
    ) {
        return Ok(json!({
            "allowed": true,
            "mode": "pass-through",
            "command": command,
            "reasons": [],
            "replacement": Value::Null,
        }));
    }
    let joined = command.join(" ").to_lowercase();
    if DESTRUCTIVE_PATTERNS
        .iter()
        .any(|pattern| joined.contains(pattern))
    {
        return Ok(json!({
            "allowed": false,
            "mode": "blocked",
            "command": command,
            "reasons": ["destructive-command"],
            "replacement": Value::Null,
        }));
    }
    if let Some(cwd) = payload.get("cwd").and_then(Value::as_str) {
        let candidate = Path::new(cwd);
        let absolute = if candidate.is_absolute() {
            candidate.to_path_buf()
        } else {
            project_root.join(candidate)
        };
        let normalized = absolute
            .canonicalize()
            .unwrap_or_else(|_| absolute.to_path_buf());
        let project = project_root
            .canonicalize()
            .unwrap_or_else(|_| project_root.to_path_buf());
        if !normalized.starts_with(&project) {
            return Ok(json!({
                "allowed": false,
                "mode": "blocked",
                "command": command,
                "reasons": ["cwd-outside-project"],
                "replacement": Value::Null,
            }));
        }
    }
    Ok(json!({
        "allowed": true,
        "mode": "allow",
        "command": command,
        "reasons": [],
        "replacement": Value::Null,
    }))
}

fn cache_health() -> Value {
    json!({
        "plans": 0,
        "active": 0,
        "refresh_due": 0,
        "expired": 0,
        "cacheable_tokens": 0,
    })
}

fn session_start(payload: &Map<String, Value>, project_root: &Path) -> Result<Value, String> {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "HOOK_SYSTEM_TIME_INVALID".to_owned())?
        .as_secs_f64();
    let task = payload
        .get("task")
        .or_else(|| payload.get("prompt"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    Ok(json!({
        "mode": "activate-runtime",
        "project": project_root.to_string_lossy(),
        "task": task,
        "required_actions": ["runtime-status", "structural-index", "session-open", "competitive-fabric-profile"],
        "optimization_mode": "full",
        "statusline": "[SYN:FULL] ⇩0",
        "cache_health": cache_health(),
        "cache_action": "preserve-stable-prefix",
        "delegation": Value::Null,
        "timestamp": timestamp,
    }))
}

fn prompt_submit(payload: &Map<String, Value>) -> Value {
    let prompt = payload
        .get("prompt")
        .or_else(|| payload.get("text"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    let lower = prompt.to_lowercase();
    let risk = if ["security", "auth", "permission", "secret", "crypto"]
        .iter()
        .any(|word| lower.contains(word))
    {
        "security-critical"
    } else {
        "normal"
    };
    json!({
        "mode": "classify-task",
        "risk": risk,
        "prompt_bytes": prompt.len(),
        "optimization_mode": "full",
        "delegation": Value::Null,
        "memory_observations": [],
        "memory_extraction_error": "",
        "cache_health": cache_health(),
        "cache_action": "preserve-stable-prefix",
    })
}

fn session_id(payload: &Map<String, Value>) -> Value {
    payload.get("session_id").cloned().unwrap_or(Value::Null)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let selected = phase(arguments)?;
    let payload = payload(arguments)?;
    match selected {
        "pre" => pre_tool(&payload, project_root),
        "post" => native_hook_output::post_tool(&payload, project_root, state_root),
        "session-start" => session_start(&payload, project_root),
        "prompt" => Ok(prompt_submit(&payload)),
        "pre-compact" => Ok(json!({
            "mode": "checkpoint-before-compact",
            "session_id": session_id(&payload),
            "required": ["exact-history-checkpoint", "summary-dag-root", "evidence-handles"],
        })),
        "post-compact" => Ok(json!({
            "mode": "verify-after-compact",
            "session_id": session_id(&payload),
            "checks": ["history-chain", "summary-expansion", "mandatory-context-roles"],
        })),
        "stop" => Ok(json!({
            "mode": "flush-runtime",
            "session_id": session_id(&payload),
            "actions": ["flush-events", "checkpoint", "drain-completions", "flush-fabric-insights"],
        })),
        "session-end" => Ok(json!({
            "mode": "close-session",
            "session_id": session_id(&payload),
            "actions": ["final-checkpoint", "claim-boundary", "release-locks", "final-fabric-metrics"],
            "memory_observations": [],
            "memory_extraction_error": "",
        })),
        _ => Err(format!("HOOK_PHASE_INVALID:{selected}")),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn hook_is_a_single_public_route() {
        assert!(supports(&["hook".to_owned()]));
    }
}
