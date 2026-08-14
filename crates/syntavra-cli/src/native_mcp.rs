#![forbid(unsafe_code)]
#![allow(clippy::pedantic, clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::{self, BufRead as _, Write as _};
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const CATALOG: &str = include_str!("../../../contracts/engine/mcp-native-catalog-v1.json");
const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "mcp")
}

fn canonical_into(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(
            &serde_json::to_string(value)
                .map_err(|error| format!("MCP_JSON_STRING_FAILED:{error}"))?,
        ),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                canonical_into(value, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            for (index, (key, value)) in ordered.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(
                    &serde_json::to_string(key)
                        .map_err(|error| format!("MCP_JSON_KEY_FAILED:{error}"))?,
                );
                output.push(':');
                canonical_into(value, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

fn canonical(value: &Value) -> Result<String, String> {
    let mut output = String::new();
    canonical_into(value, &mut output)?;
    Ok(output)
}

fn hash_json(value: &Value) -> Result<String, String> {
    Ok(sha256_hex(canonical(value)?.as_bytes()))
}

fn option(arguments: &[String], name: &str) -> Option<String> {
    let prefix = format!("{name}=");
    for (index, value) in arguments.iter().enumerate() {
        if value == name {
            return arguments.get(index + 1).cloned();
        }
        if let Some(value) = value.strip_prefix(&prefix) {
            return Some(value.to_owned());
        }
    }
    None
}

fn installed_profile(state_root: &Path) -> Option<String> {
    let value = fs::read_to_string(state_root.join("mcp-profile.json")).ok()?;
    serde_json::from_str::<Value>(&value)
        .ok()?
        .get("name")?
        .as_str()
        .map(str::to_owned)
}

fn normalize_profile(value: &str) -> Result<&'static str, String> {
    match value.trim().to_lowercase().as_str() {
        "minimal" | "tiny" => Ok("minimal"),
        "balanced" | "optimized" => Ok("balanced"),
        "audit" | "full" => Ok("audit"),
        other => Err(format!("MCP_PROFILE_INVALID:{other}")),
    }
}

fn active_profile(arguments: &[String], state_root: &Path) -> Result<&'static str, String> {
    let requested = env::var("SYNTAVRA_MCP_PROFILE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| option(arguments, "--mcp-profile"))
        .or_else(|| installed_profile(state_root))
        .unwrap_or_else(|| "minimal".to_owned());
    normalize_profile(&requested)
}

fn schema_mode() -> Result<&'static str, String> {
    let requested = env::var("SYNTAVRA_SCHEMA_MODE")
        .unwrap_or_else(|_| "compact".to_owned())
        .trim()
        .to_lowercase();
    match requested.as_str() {
        "compact" => Ok("compact"),
        "raw" => Ok("raw"),
        other => Err(format!("MCP_SCHEMA_MODE_INVALID:{other}")),
    }
}

fn catalog() -> Result<Value, String> {
    serde_json::from_str(CATALOG).map_err(|error| format!("MCP_CATALOG_INVALID:{error}"))
}

fn profile_row<'a>(catalog: &'a Value, profile: &str) -> Result<&'a Value, String> {
    catalog
        .get("profiles")
        .and_then(|value| value.get(profile))
        .ok_or_else(|| format!("MCP_CATALOG_PROFILE_MISSING:{profile}"))
}

fn exposed_tools(catalog: &Value, profile: &str, mode: &str) -> Result<Value, String> {
    profile_row(catalog, profile)?
        .get(if mode == "raw" {
            "raw_tools"
        } else {
            "compiled_tools"
        })
        .cloned()
        .ok_or_else(|| "MCP_CATALOG_TOOLS_MISSING".to_owned())
}

fn manifest(catalog: &Value, profile: &str, mode: &str) -> Result<Value, String> {
    profile_row(catalog, profile)?
        .get(if mode == "raw" {
            "raw_manifest"
        } else {
            "compact_manifest"
        })
        .cloned()
        .ok_or_else(|| "MCP_CATALOG_MANIFEST_MISSING".to_owned())
}

fn tool_names(tools: &Value) -> BTreeSet<String> {
    tools
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| row.get("name").and_then(Value::as_str).map(str::to_owned))
        .collect()
}

fn authorization(arguments: &Map<String, Value>) -> (bool, bool, bool) {
    let raw = arguments
        .get("_syntavra_authorization")
        .and_then(Value::as_object);
    let user_authorized = raw
        .and_then(|value| value.get("user_authorized"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || arguments
            .get("_approved")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    let exact_evidence = raw
        .and_then(|value| value.get("exact_evidence"))
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let sandboxed = raw
        .and_then(|value| value.get("sandboxed"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    (user_authorized, exact_evidence, sandboxed)
}

fn risk(tool: &str, arguments: &Map<String, Value>) -> &'static str {
    const SAFE_STATE_WRITES: &[&str] = &[
        "syntavra.session.open",
        "syntavra.session.append",
        "syntavra.session.compact",
        "syntavra.session.checkpoint",
        "syntavra.session.fork",
        "syntavra.session.merge",
        "syntavra.output.capture",
        "syntavra.usage.record",
        "syntavra.usage.attribution.record",
        "syntavra.provider.prepare",
        "syntavra.provider.capture",
        "syntavra.policy.record",
        "syntavra.data.route",
    ];
    const SANDBOX_EXECUTION: &[&str] = &["syntavra.sandbox.execute", "syntavra.sandbox.batch"];
    if SAFE_STATE_WRITES.contains(&tool) {
        return "safe-state-write";
    }
    if SANDBOX_EXECUTION.contains(&tool) {
        return "sandbox-execute";
    }
    if tool == "syntavra.process.submit" {
        return "unsandboxed-execute";
    }
    if matches!(
        tool,
        "syntavra.evidence.rotate_key" | "syntavra.backup.create"
    ) || (tool == "syntavra.evidence.gc"
        && !arguments
            .get("dry_run")
            .and_then(Value::as_bool)
            .unwrap_or(true))
    {
        return "destructive";
    }
    if tool.contains("provider.invoke")
        || tool.contains("provider.request")
        || tool.contains("network.")
    {
        return "network";
    }
    if [".install", ".uninstall", ".rollback", ".migrate", ".apply"]
        .iter()
        .any(|part| tool.contains(part))
    {
        return "destructive";
    }
    "read-or-plan"
}

fn decision(
    tool: &str,
    arguments: &Map<String, Value>,
    profile: &str,
    exposed: &BTreeSet<String>,
) -> Result<Value, String> {
    let legacy_profile = match profile {
        "minimal" => "tiny",
        "balanced" => "optimized",
        _ => "full",
    };
    let listed = exposed.contains(tool);
    let (user_authorized, exact_evidence, declared_sandboxed) = authorization(arguments);
    let risk = risk(tool, arguments);
    let sandboxed =
        declared_sandboxed || matches!(tool, "syntavra.sandbox.execute" | "syntavra.sandbox.batch");
    let mut allowed = listed;
    let mut reason = "profile-listed";
    if !listed {
        allowed = false;
        reason = "tool-not-exposed-by-active-profile";
    } else if matches!(
        risk,
        "destructive" | "network" | "sandbox-execute" | "unsandboxed-execute"
    ) {
        if !exact_evidence {
            allowed = false;
            reason = "exact-evidence-required";
        } else if !user_authorized {
            allowed = false;
            reason = "explicit-user-authorization-required";
        } else if risk == "sandbox-execute" && !sandboxed {
            allowed = false;
            reason = "sandbox-required";
        } else if risk == "unsandboxed-execute"
            && env::var("SYNTAVRA_ALLOW_UNSANDBOXED_PROCESS").as_deref() != Ok("1")
        {
            allowed = false;
            reason = "unsandboxed-process-disabled";
        } else {
            reason = "authorized-risky-operation";
        }
    } else if risk == "safe-state-write" && !exact_evidence {
        allowed = false;
        reason = "exact-evidence-required";
    }
    let body = json!({
        "version": VERSION,
        "channel": CHANNEL,
        "tool": tool,
        "profile": profile,
        "legacy_profile": legacy_profile,
        "risk": risk,
        "allowed": allowed,
        "reason": reason,
        "listed": listed,
        "user_authorized": user_authorized,
        "exact_evidence": exact_evidence,
        "sandboxed": sandboxed,
    });
    let mut value = body
        .as_object()
        .cloned()
        .ok_or_else(|| "MCP_DECISION_OBJECT_FAILED".to_owned())?;
    value.remove("version");
    value.remove("channel");
    value.insert("receipt_hash".to_owned(), Value::String(hash_json(&body)?));
    Ok(Value::Object(value))
}

fn runtime_status(project_root: &Path, state_root: &Path) -> Value {
    let package = project_root.join("syntavra_runtime");
    let checks = json!({
        "skill_installed": project_root.join("skills/syntavra/SKILL.md").is_file(),
        "runtime_package": package.join("__init__.py").is_file(),
        "state_store": state_root.exists() || fs::create_dir_all(state_root).is_ok(),
        "evidence_store": state_root.join("evidence").exists() || fs::create_dir_all(state_root.join("evidence")).is_ok(),
        "host_adapter": true,
        "rollout_available": true,
    });
    let healthy = checks
        .as_object()
        .is_some_and(|values| values.values().all(|value| value == &Value::Bool(true)));
    json!({
        "state": if healthy { "RUNTIME_ACTIVE" } else { "RUNTIME_DEGRADED" },
        "healthy": healthy,
        "checks": checks,
        "reasons": [],
        "details": {
            "version": VERSION,
            "release_channel": CHANNEL,
            "host": "codex",
            "host_negotiation": {"mode": "NATIVE_MCP"},
            "rollout_candidates": [],
            "enforcement_boundary": "NATIVE_MCP",
            "runtime_plane": "pre-release-001",
        },
    })
}

fn safe_call(tool: &str, project_root: &Path, state_root: &Path) -> Result<Value, String> {
    match tool {
        "syntavra.status" => Ok(runtime_status(project_root, state_root)),
        _ => Err("MCP_NATIVE_TOOL_NOT_IMPLEMENTED".to_owned()),
    }
}

fn result_response(
    request_id: Value,
    value: Value,
    decision: &Value,
    manifest: &Value,
) -> Result<Value, String> {
    let value_hash = hash_json(&value)?;
    let text = serde_json::to_string(&value)
        .map_err(|error| format!("MCP_RESULT_SERIALIZE_FAILED:{error}"))?;
    Ok(json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "_meta": {
                "syntavra_route_receipt": decision["receipt_hash"],
                "syntavra_profile": decision["profile"],
                "syntavra_risk": decision["risk"],
                "syntavra_schema_mode": manifest["schema"]["mode"],
                "syntavra_schema_compilation": manifest["schema"]["compilation"],
                "syntavra_secret_redaction": {
                    "redacted": false,
                    "count": 0,
                    "types": [],
                    "fingerprints": [],
                    "original_hash": value_hash,
                    "redacted_hash": value_hash,
                },
                "syntavra_wire": {"encoding": "json", "savings_ratio": 0.0},
            },
        },
    }))
}

fn handle(
    message: &Value,
    catalog: &Value,
    profile: &str,
    mode: &str,
    project_root: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    let method = message
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let request_id = message.get("id").cloned().unwrap_or(Value::Null);
    if method == "notifications/initialized" {
        return Ok(None);
    }
    if method == "initialize" {
        let protocol = message
            .get("params")
            .and_then(Value::as_object)
            .and_then(|value| value.get("protocolVersion"))
            .cloned()
            .unwrap_or_else(|| Value::String("2025-06-18".to_owned()));
        return Ok(Some(json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "syntavra", "version": VERSION},
                "instructions": "Token/context optimization with exact recovery and fail-closed tool routing.",
            },
        })));
    }
    if method == "tools/list" {
        return Ok(Some(json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": exposed_tools(catalog, profile, mode)?,
                "_meta": {"syntavra": manifest(catalog, profile, mode)?},
            },
        })));
    }
    if method == "tools/call" {
        let params = message
            .get("params")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let tool = params
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let arguments = params
            .get("arguments")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let tools = exposed_tools(catalog, profile, mode)?;
        let decision = decision(tool, &arguments, profile, &tool_names(&tools))?;
        if decision["allowed"] != Value::Bool(true) {
            return Ok(Some(json!({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32001,
                    "message": format!("PermissionError: {}", decision["reason"].as_str().unwrap_or("denied")),
                    "data": decision,
                },
            })));
        }
        let active_manifest = manifest(catalog, profile, mode)?;
        return match safe_call(tool, project_root, state_root) {
            Ok(value) => result_response(request_id, value, &decision, &active_manifest).map(Some),
            Err(_) => Ok(Some(json!({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "Syntavra tool execution failed"},
            }))),
        };
    }
    if method == "ping" {
        return Ok(Some(
            json!({"jsonrpc": "2.0", "id": request_id, "result": {}}),
        ));
    }
    Ok(Some(json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    })))
}

fn parse_error_message(line: &str, error: &serde_json::Error) -> String {
    let trimmed = line.trim();
    if trimmed == "{" {
        return "Expecting property name enclosed in double quotes: line 2 column 1 (char 2)"
            .to_owned();
    }
    format!(
        "JSONDecodeError: {}: line {} column {}",
        error,
        error.line(),
        error.column()
    )
}

pub fn serve(arguments: &[String], project_root: &Path, state_root: &Path) -> ! {
    let catalog = match catalog() {
        Ok(value) => value,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(4);
        }
    };
    let profile = match active_profile(arguments, state_root) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(4);
        }
    };
    let mode = match schema_mode() {
        Ok(value) => value,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(4);
        }
    };
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(value) => value,
            Err(error) => {
                eprintln!("MCP_STDIN_READ_FAILED:{error}");
                std::process::exit(4);
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Value>(&line) {
            Ok(message) => {
                match handle(&message, &catalog, profile, mode, project_root, state_root) {
                    Ok(value) => value,
                    Err(error) => {
                        eprintln!("{error}");
                        std::process::exit(4);
                    }
                }
            }
            Err(error) => Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {"code": -32700, "message": parse_error_message(&line, &error)},
            })),
        };
        if let Some(response) = response {
            let rendered = match serde_json::to_string(&response) {
                Ok(value) => value,
                Err(error) => {
                    eprintln!("MCP_RESPONSE_SERIALIZE_FAILED:{error}");
                    std::process::exit(4);
                }
            };
            if writeln!(stdout, "{rendered}").is_err() || stdout.flush().is_err() {
                std::process::exit(4);
            }
        }
    }
    std::process::exit(0)
}
