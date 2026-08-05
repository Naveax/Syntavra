#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::env;
use std::fs;
use std::path::Path;

use rusqlite::Connection;
use serde_json::{json, Map, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "status")
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn minimal_profile() -> Value {
    json!({
        "name": "minimal",
        "exposed_tools": [
            "syntavra.status",
            "syntavra.inspect.map",
            "syntavra.output.capture",
            "syntavra.output.search",
            "syntavra.output.reveal",
            "syntavra.session.semantic_context",
            "syntavra.fabric.route",
            "syntavra.fabric.doctor",
        ],
        "max_active_tools": 8,
        "tool_description_budget_tokens": 800,
        "default_timeout_seconds": 120,
        "require_routing_receipt": true,
        "require_exact_evidence": true,
        "allow_unknown_tools": false,
    })
}

fn active_profile(state_root: &Path) -> Value {
    let path = state_root.join("mcp-profile.json");
    if !path.is_file() {
        return minimal_profile();
    }
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .unwrap_or_else(|| json!({"name": "minimal", "invalid_profile_file": true}))
}

fn initialize_sessions(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("STATUS_SESSION_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("STATUS_SESSION_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS sessions(\
               session_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,parent_ids_json TEXT NOT NULL,\
               state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,metadata_json TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS session_events(\
               session_id TEXT NOT NULL,sequence INTEGER NOT NULL,event_type TEXT NOT NULL,\
               payload_json TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL,\
               created_at REAL NOT NULL,PRIMARY KEY(session_id,sequence),UNIQUE(session_id,event_hash),\
               FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS session_event_hash_idx ON session_events(event_hash);\
             CREATE TABLE IF NOT EXISTS session_summaries(\
               summary_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,content TEXT NOT NULL,\
               source_start INTEGER NOT NULL,source_end INTEGER NOT NULL,child_ids_json TEXT NOT NULL,\
               source_hash TEXT NOT NULL,order_level INTEGER NOT NULL,created_at REAL NOT NULL,\
               invalidated_at REAL,FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS session_summary_range_idx \
               ON session_summaries(session_id,source_start,source_end);\
             CREATE TABLE IF NOT EXISTS session_checkpoints(\
               checkpoint_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,through_sequence INTEGER NOT NULL,\
               root_summary_id TEXT,event_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,created_at REAL NOT NULL,\
               FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE);\
             CREATE TABLE IF NOT EXISTS session_quarantine(\
               quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,\
               object_type TEXT NOT NULL,object_id TEXT NOT NULL,reason TEXT NOT NULL,\
               payload_json TEXT NOT NULL,created_at REAL NOT NULL);",
        )
        .map_err(|error| format!("STATUS_SESSION_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn sessions(state_root: &Path) -> Result<Vec<Value>, String> {
    let connection = initialize_sessions(&state_root.join("sessions.sqlite3"))?;
    let mut statement = connection
        .prepare(
            "SELECT session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json \
             FROM sessions ORDER BY updated_at DESC",
        )
        .map_err(|error| format!("STATUS_SESSION_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, f64>(4)?,
                row.get::<_, f64>(5)?,
                row.get::<_, String>(6)?,
            ))
        })
        .map_err(|error| format!("STATUS_SESSION_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let row = row.map_err(|error| format!("STATUS_SESSION_ROW_FAILED:{error}"))?;
        let parents = serde_json::from_str::<Value>(&row.2)
            .map_err(|_| "STATUS_SESSION_PARENT_IDS_INVALID".to_owned())?;
        let metadata = serde_json::from_str::<Value>(&row.6)
            .map_err(|_| "STATUS_SESSION_METADATA_INVALID".to_owned())?;
        output.push(json!({
            "session_id": row.0,
            "project_id": row.1,
            "parent_ids": parents,
            "state": row.3,
            "created_at": row.4,
            "updated_at": row.5,
            "metadata": metadata,
        }));
    }
    Ok(output)
}

fn memory_status(state_root: &Path, stats: &Value) -> Result<Value, String> {
    Ok(json!({
        "version": VERSION,
        "channel": CHANNEL,
        "worker_alive": false,
        "last_cycle": {
            "state": "IDLE",
            "started_at": Value::Null,
            "completed_at": Value::Null,
            "wall_time_ms": 0.0,
            "compacted": 0,
            "failures": [],
        },
        "analytics": stats.get("session_analytics").cloned().unwrap_or_else(|| json!({})),
        "sessions": sessions(state_root)?,
    }))
}

fn evidence(stats: &Value) -> Value {
    json!({
        "provider_usage": stats.get("provider_usage_integrity").cloned().unwrap_or_else(|| json!({})),
        "token_attribution": stats.get("token_attribution").cloned().unwrap_or_else(|| json!({})),
        "claim_boundary": {
            "external_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "live_integration": "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN",
            "public_maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
        },
    })
}

fn executable_exists(name: &str) -> bool {
    let path = env::var_os("PATH").unwrap_or_default();
    let suffixes: &[&str] = if cfg!(windows) {
        &[".exe", ".cmd", ".bat", ""]
    } else {
        &[""]
    };
    env::split_paths(&path).any(|directory| {
        suffixes
            .iter()
            .any(|suffix| directory.join(format!("{name}{suffix}")).is_file())
    })
}

fn home_dir() -> Option<std::path::PathBuf> {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(std::path::PathBuf::from)
}

fn candidate_exists(candidate: &str) -> bool {
    if let Some(rest) = candidate.strip_prefix("~/") {
        return home_dir().is_some_and(|home| home.join(rest).exists());
    }
    Path::new(candidate).exists()
}

fn detected_adapters() -> Vec<String> {
    let rows: &[(&str, &[&str], &[&str])] = &[
        (
            "claude-code",
            &["claude"],
            &["~/.claude/settings.json", ".claude/settings.json"],
        ),
        ("codex", &["codex"], &["~/.codex/config.toml", "AGENTS.md"]),
        (
            "gemini-cli",
            &["gemini"],
            &["~/.gemini/settings.json", "GEMINI.md"],
        ),
        ("vscode-copilot", &[], &[".vscode/mcp.json"]),
        ("jetbrains-copilot", &[], &[".idea/mcp.json"]),
        (
            "cursor",
            &["cursor"],
            &[".cursor/rules/syntavra.mdc", ".cursor/mcp.json"],
        ),
        (
            "windsurf",
            &["windsurf"],
            &[".windsurfrules", ".codeium/windsurf/mcp_config.json"],
        ),
        (
            "opencode",
            &["opencode"],
            &["opencode.json", "~/.config/opencode/opencode.json"],
        ),
        ("cline", &[], &[".clinerules", ".vscode/mcp.json"]),
        (
            "roo-code",
            &[],
            &[".roo/rules/syntavra.md", ".vscode/mcp.json"],
        ),
        (
            "qwen-code",
            &["qwen", "qwen-code"],
            &["QWEN.md", "~/.qwen/settings.json"],
        ),
        (
            "kiro",
            &["kiro", "kiro-cli", "q"],
            &[".kiro/settings/mcp.json", ".kiro/skills/syntavra/SKILL.md"],
        ),
        (
            "zed",
            &["zed"],
            &[".zed/settings.json", "~/.config/zed/settings.json"],
        ),
        (
            "pi",
            &["pi"],
            &[".pi/settings.json", ".pi/skills/syntavra/SKILL.md"],
        ),
        (
            "omp",
            &["omp"],
            &[".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"],
        ),
        (
            "openclaw",
            &["openclaw"],
            &[
                "skills/syntavra/SKILL.md",
                ".openclaw/skills/syntavra/SKILL.md",
            ],
        ),
        (
            "aider",
            &["aider"],
            &[".aider.conf.yml", "~/.aider.conf.yml"],
        ),
        (
            "continue",
            &["continue"],
            &[".continue/config.yaml", "~/.continue/config.yaml"],
        ),
    ];
    rows.iter()
        .filter(|(_, commands, candidates)| {
            commands.iter().any(|command| executable_exists(command))
                || candidates
                    .iter()
                    .any(|candidate| candidate_exists(candidate))
        })
        .map(|(host, _, _)| (*host).to_owned())
        .collect()
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("STATUS_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("STATUS_STATE_CREATE_FAILED:{error}"))?;

    let doctor_command = vec!["doctor".to_owned()];
    let mut doctor =
        super::native_operator_lifecycle::execute(&doctor_command, project_root, state_root)?;
    if let Some(value) = doctor.value.as_object_mut() {
        value.insert("detected_adapters".to_owned(), json!(detected_adapters()));
    }
    let stats = super::native_stats::execute(project_root, state_root)?;
    let profile = active_profile(state_root);
    let evidence = evidence(&stats);

    let mut focused = Map::new();
    if flag(arguments, "--doctor") {
        focused.insert("doctor".to_owned(), doctor.value.clone());
    }
    if flag(arguments, "--savings") {
        focused.insert(
            "savings".to_owned(),
            stats
                .get("token_attribution")
                .cloned()
                .unwrap_or_else(|| json!({})),
        );
    }
    if flag(arguments, "--profile") {
        focused.insert("profile".to_owned(), profile.clone());
    }
    if flag(arguments, "--memory") {
        focused.insert("memory".to_owned(), memory_status(state_root, &stats)?);
    }
    if flag(arguments, "--evidence") {
        focused.insert("evidence".to_owned(), evidence.clone());
    }

    let value = if focused.is_empty() {
        json!({
            "product": "Syntavra",
            "version": VERSION,
            "channel": CHANNEL,
            "role": "token-and-context-optimization-skill",
            "doctor": doctor.value,
            "stats": stats,
            "savings": evidence["token_attribution"],
            "profile": profile,
            "readiness": {
                "ok": false,
                "claim": "DAILY_CODING_AGENT_READINESS_NOT_PROVEN",
            },
            "evidence": evidence,
            "primary_workflow": ["setup", "status", "run", "prove"],
        })
    } else {
        let mut output = Map::new();
        output.insert("product".to_owned(), Value::String("Syntavra".to_owned()));
        output.insert("version".to_owned(), Value::String(VERSION.to_owned()));
        output.insert("channel".to_owned(), Value::String(CHANNEL.to_owned()));
        output.extend(focused);
        Value::Object(output)
    };

    Ok(Decision {
        value,
        exit_code: if doctor.value.get("ok").and_then(Value::as_bool) == Some(true) {
            0
        } else {
            2
        },
    })
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn status_route_is_supported() {
        assert!(supports(&["status".to_owned()]));
    }
}
