#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const LONG_RUNNING: &[&str] = &[
    "cargo", "cmake", "ctest", "docker", "dotnet", "go", "gradle", "jest", "make",
    "mvn", "ninja", "npm", "nox", "pnpm", "podman", "py.test", "pytest", "ruff",
    "terraform", "tox", "vitest", "yarn",
];
const NETWORK: &[&str] = &[
    "cargo", "curl", "gh", "git", "invoke-webrequest", "iwr", "npm", "pip", "pnpm",
    "uv", "wget", "yarn",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, route] if fabric == "fabric" && route == "route")
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let prefix = format!("{name}=");
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let value = if item == name {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{name}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(&prefix).map(str::to_owned)
        };
        if let Some(value) = value {
            if found.is_some() {
                return Err(format!("{name}_DUPLICATE"));
            }
            found = Some(value);
        }
        index += 1;
    }
    Ok(found)
}

fn route_arguments(arguments: &[String]) -> Result<(bool, bool, Option<PathBuf>, Vec<String>), String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "route")
        .ok_or_else(|| "FABRIC_ROUTE_COMMAND_MISSING".to_owned())?
        + 2;
    let mut network_untrusted = false;
    let mut repeated = false;
    let mut output = None;
    let mut command = Vec::new();
    let mut index = start;
    while index < arguments.len() {
        let item = &arguments[index];
        if item == "--" {
            command.extend(arguments[index + 1..].iter().cloned());
            break;
        }
        if item == "--network-untrusted" {
            network_untrusted = true;
            index += 1;
            continue;
        }
        if item == "--repeated" {
            repeated = true;
            index += 1;
            continue;
        }
        if item == "--output" {
            index += 1;
            let value = arguments
                .get(index)
                .ok_or_else(|| "--output_VALUE_MISSING".to_owned())?;
            if output.is_some() {
                return Err("--output_DUPLICATE".to_owned());
            }
            output = Some(PathBuf::from(value));
            index += 1;
            continue;
        }
        if let Some(value) = item.strip_prefix("--output=") {
            if output.is_some() {
                return Err("--output_DUPLICATE".to_owned());
            }
            output = Some(PathBuf::from(value));
            index += 1;
            continue;
        }
        command.extend(arguments[index..].iter().cloned());
        break;
    }
    if command.is_empty() {
        return Err("command argv is required after --".to_owned());
    }
    Ok((network_untrusted, repeated, output, command))
}

fn executable(argv: &[String]) -> String {
    argv.first()
        .and_then(|value| {
            value
                .rsplit(['/', '\\'])
                .next()
                .map(str::to_ascii_lowercase)
        })
        .unwrap_or_default()
}

fn family(argv: &[String]) -> &'static str {
    if argv.is_empty() {
        return "empty";
    }
    let exe = executable(argv);
    let joined = argv.join(" ").to_ascii_lowercase();
    if exe == "git" {
        return "git";
    }
    if exe == "gh" {
        return "github";
    }
    if matches!(exe.as_str(), "pytest" | "py.test" | "jest" | "vitest" | "ctest")
        || format!(" {joined}").contains(" test")
    {
        return "test";
    }
    if matches!(exe.as_str(), "grep" | "rg" | "find" | "fd" | "ls" | "tree") {
        return "search";
    }
    if matches!(exe.as_str(), "cat" | "head" | "tail" | "sed" | "type" | "get-content") {
        return "read";
    }
    if matches!(exe.as_str(), "npm" | "pnpm" | "yarn" | "pip" | "uv" | "cargo") {
        return "package";
    }
    if matches!(exe.as_str(), "kubectl" | "aws" | "gcloud" | "az") {
        return "cloud";
    }
    if matches!(exe.as_str(), "docker" | "podman") {
        return "container";
    }
    if matches!(exe.as_str(), "curl" | "wget" | "iwr" | "invoke-webrequest") {
        return "network";
    }
    if matches!(exe.as_str(), "make" | "cmake" | "ninja" | "gradle" | "mvn" | "dotnet" | "go") {
        return "build";
    }
    "generic"
}

fn destructive(argv: &[String]) -> bool {
    let lower = argv
        .iter()
        .map(|value| value.to_ascii_lowercase())
        .collect::<Vec<_>>();
    let exe = executable(argv);
    if exe == "rm"
        && lower.get(1).is_some_and(|value| value == "-rf")
        && lower
            .get(2)
            .is_some_and(|value| value.starts_with('/') || value.starts_with('~'))
    {
        return true;
    }
    if exe == "git"
        && lower.get(1).is_some_and(|value| value == "reset")
        && lower.get(2).is_some_and(|value| value == "--hard")
    {
        return true;
    }
    if exe == "git" && lower.get(1).is_some_and(|value| value == "clean") {
        if let Some(flag) = lower.get(2) {
            if flag.starts_with('-')
                && flag
                    .chars()
                    .any(|value| matches!(value, 'f' | 'x' | 'd'))
            {
                return true;
            }
        }
    }
    if exe.starts_with("mkfs.") {
        return true;
    }
    if exe == "format"
        && lower.get(1).is_some_and(|value| {
            let bytes = value.as_bytes();
            bytes.len() >= 2 && bytes[0].is_ascii_lowercase() && bytes[1] == b':'
        })
    {
        return true;
    }
    if exe == "remove-item" {
        let joined = lower.join(" ");
        return joined.contains("-recurse")
            && joined.contains("-force")
            && lower.iter().any(|value| {
                let bytes = value.as_bytes();
                bytes.len() >= 3
                    && bytes[0].is_ascii_lowercase()
                    && bytes[1] == b':'
                    && bytes[2] == b'\\'
            });
    }
    false
}

fn repeat_hash(argv: &[String], family: &str) -> Result<String, String> {
    let canonical = serde_json::to_string(&json!({"argv": argv, "family": family}))
        .map_err(|error| format!("FABRIC_ROUTE_HASH_INPUT_FAILED:{error}"))?;
    Ok(sha256_hex(canonical.as_bytes()))
}

fn decision(argv: &[String], network_untrusted: bool, repeated: bool) -> Result<Value, String> {
    let family = family(argv);
    let exe = executable(argv);
    let long_running = LONG_RUNNING.contains(&exe.as_str()) || matches!(family, "test" | "build");
    let sandbox = network_untrusted && NETWORK.contains(&exe.as_str());
    let capture = family != "empty";
    if destructive(argv) {
        return Ok(json!({
            "family": family,
            "mode": "blocked",
            "normalized_command": argv,
            "replacement_argv": [],
            "recommended_tools": [],
            "reasons": ["destructive-command"],
            "capture_required": false,
            "background": false,
            "sandbox": false,
            "repeat_key": sha256_hex(argv.join(" ").as_bytes()),
            "safe_to_rewrite": false,
        }));
    }

    let mut recommended = Vec::<String>::new();
    if capture {
        recommended.push("syntavra.output.capture".to_owned());
    }
    if matches!(family, "search" | "read") {
        recommended.push("syntavra.inspect.map".to_owned());
        recommended.push("syntavra.output.search".to_owned());
    }
    if matches!(family, "test" | "build") {
        recommended.push("syntavra.process.submit".to_owned());
        recommended.push("syntavra.process.completions".to_owned());
    }
    if sandbox {
        recommended.insert(0, "syntavra.sandbox.execute".to_owned());
    }
    let mut reasons = Vec::<String>::new();
    if repeated && matches!(family, "read" | "search" | "git" | "github") {
        reasons.push("repeat-read-elision-eligible".to_owned());
        recommended.push("syntavra.output.search".to_owned());
    }
    let mut seen = BTreeSet::new();
    recommended.retain(|value| seen.insert(value.clone()));

    let (mode, replacement) = if sandbox {
        reasons.push("untrusted-network-command".to_owned());
        let mut value = vec![
            "syntavra".to_owned(),
            "sandbox".to_owned(),
            "execute".to_owned(),
            "--".to_owned(),
        ];
        value.extend(argv.iter().cloned());
        ("sandbox-replace", value)
    } else if long_running {
        reasons.push("long-running-command".to_owned());
        let mut value = vec![
            "syntavra".to_owned(),
            "run".to_owned(),
            "--background".to_owned(),
            "--".to_owned(),
        ];
        value.extend(argv.iter().cloned());
        ("background-replace", value)
    } else {
        reasons.push("exact-command-preserved".to_owned());
        ("execute-and-capture", Vec::new())
    };
    Ok(json!({
        "family": family,
        "mode": mode,
        "normalized_command": argv,
        "replacement_argv": replacement,
        "recommended_tools": recommended,
        "reasons": reasons,
        "capture_required": capture,
        "background": long_running,
        "sandbox": sandbox,
        "repeat_key": repeat_hash(argv, family)?,
        "safe_to_rewrite": true,
    }))
}

fn record_event(
    state_root: &Path,
    host: &str,
    repeated: bool,
    value: &Value,
    latency_ms: f64,
) -> Result<(), String> {
    fs::create_dir_all(state_root).map_err(|error| format!("FABRIC_STATE_CREATE_FAILED:{error}"))?;
    let database = state_root.join("competitive-fabric.sqlite3");
    let connection = Connection::open(database)
        .map_err(|error| format!("FABRIC_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS fabric_events(\
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,\
                event_type TEXT NOT NULL,\
                family TEXT NOT NULL,\
                host TEXT NOT NULL,\
                raw_bytes INTEGER NOT NULL,\
                visible_bytes INTEGER NOT NULL,\
                latency_ms REAL NOT NULL,\
                success INTEGER NOT NULL,\
                cache_hit INTEGER NOT NULL,\
                metadata_json TEXT NOT NULL,\
                created_at REAL NOT NULL\
            );\
            CREATE INDEX IF NOT EXISTS fabric_event_type_idx \
                ON fabric_events(event_type,created_at);\
            CREATE INDEX IF NOT EXISTS fabric_family_idx \
                ON fabric_events(family,created_at);",
        )
        .map_err(|error| format!("FABRIC_DATABASE_SCHEMA_FAILED:{error}"))?;
    let family = value["family"].as_str().unwrap_or("generic");
    let mode = value["mode"].as_str().unwrap_or("execute-and-capture");
    let capture = value["capture_required"].as_bool().unwrap_or(false);
    let metadata = format!(
        "{{\"capture\": {}, \"mode\": {}}}",
        if capture { "true" } else { "false" },
        serde_json::to_string(mode)
            .map_err(|error| format!("FABRIC_METADATA_SERIALIZE_FAILED:{error}"))?
    );
    let success = i64::from(mode != "blocked");
    let cache_hit = i64::from(repeated);
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("FABRIC_CLOCK_FAILED:{error}"))?
        .as_secs_f64();
    connection
        .execute(
            "INSERT INTO fabric_events(\
                event_type,family,host,raw_bytes,visible_bytes,latency_ms,\
                success,cache_hit,metadata_json,created_at\
            ) VALUES(?,?,?,?,?,?,?,?,?,?)",
            params![
                "route",
                family,
                host,
                0_i64,
                0_i64,
                latency_ms.max(0.0),
                success,
                cache_hit,
                metadata,
                created_at,
            ],
        )
        .map_err(|error| format!("FABRIC_EVENT_INSERT_FAILED:{error}"))?;
    Ok(())
}

fn write_output(path: &Path, value: &Value) -> Result<Value, String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_OUTPUT_PARENT_FAILED:{error}"))?;
        }
    }
    let rendered = serde_json::to_string_pretty(value)
        .map_err(|error| format!("FABRIC_OUTPUT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(path, rendered.as_bytes())
        .map_err(|error| format!("FABRIC_OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "output": path.to_string_lossy(),
        "bytes": rendered.len(),
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let (network_untrusted, repeated, output, command) = route_arguments(arguments)?;
    let host = option_value(arguments, "--host")?.unwrap_or_else(|| "codex".to_owned());
    let started = Instant::now();
    let value = decision(&command, network_untrusted, repeated)?;
    record_event(
        state_root,
        &host,
        repeated,
        &value,
        started.elapsed().as_secs_f64() * 1000.0,
    )?;
    output.map_or(Ok(value.clone()), |path| write_output(&path, &value))
}

#[cfg(test)]
mod tests {
    use super::{decision, family};

    fn argv(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn classifies_and_rewrites_test_commands() {
        let value = decision(&argv(&["pytest", "-q"]), false, false).unwrap();
        assert_eq!(value["family"], "test");
        assert_eq!(value["mode"], "background-replace");
    }

    #[test]
    fn blocks_destructive_commands() {
        let value = decision(&argv(&["git", "reset", "--hard"]), false, false).unwrap();
        assert_eq!(value["mode"], "blocked");
        assert_eq!(value["safe_to_rewrite"], false);
    }

    #[test]
    fn route_families_match_public_vocabulary() {
        assert_eq!(family(&argv(&["rg", "token", "."])), "search");
        assert_eq!(family(&argv(&["curl", "https://example.com"])), "network");
    }
}
