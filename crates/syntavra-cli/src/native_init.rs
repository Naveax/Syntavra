#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const HEALTH_MARKER: &[u8] = b"syntavra-health-v001-pre-release";

const REQUIRED_MODULES: &[(&str, &str)] = &[
    ("process_broker", "process_broker.py"),
    ("output_firewall", "output_firewall.py"),
    ("context_governor", "context_governor.py"),
    ("hook_engine", "hooks.py"),
    ("mcp_server", "mcp_server.py"),
    ("structural_intelligence", "structural_parsers.py"),
    ("structural_intelligence_v2", "semantic_structure.py"),
    ("host_installer", "installer.py"),
    ("zero_friction", "zero_friction.py"),
    ("secure_sandbox", "sandbox.py"),
    ("reversible_compression", "compression.py"),
    ("long_session_runtime", "session_runtime.py"),
    ("unbounded_context", "infinite_context.py"),
    ("output_governor", "output_governor.py"),
    ("signalbench", "signalbench.py"),
    ("paired_benchmark", "paired_benchmark.py"),
    ("public_proof", "public_proof.py"),
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "init")
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let prefix = format!("{flag}=");
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            result = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            result = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(result)
}

fn task(arguments: &[String]) -> Result<String, String> {
    let index = arguments
        .iter()
        .position(|value| value == "init")
        .ok_or_else(|| "INIT_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .filter(|value| !value.starts_with('-'))
        .cloned()
        .ok_or_else(|| "INIT_TASK_MISSING".to_owned())
}

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf()
}

fn default_skill_root() -> PathBuf {
    let repository = repository_root().join("skills").join("syntavra");
    if repository.is_dir() {
        repository
    } else {
        repository_root()
            .join("syntavra_runtime")
            .join("bundled_skill")
    }
}

fn default_codex_home() -> PathBuf {
    if let Some(value) = env::var_os("CODEX_HOME") {
        return PathBuf::from(value);
    }
    #[cfg(windows)]
    if let Some(value) = env::var_os("USERPROFILE") {
        return PathBuf::from(value).join(".codex");
    }
    #[cfg(not(windows))]
    if let Some(value) = env::var_os("HOME") {
        return PathBuf::from(value).join(".codex");
    }
    PathBuf::from(".codex")
}

fn initialize_state(path: &Path) -> Result<bool, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("INIT_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let connection =
        Connection::open(path).map_err(|error| format!("INIT_STATE_OPEN_FAILED:{error}"))?;
    connection
        .busy_timeout(std::time::Duration::from_secs(30))
        .map_err(|error| format!("INIT_STATE_BUSY_TIMEOUT_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS jobs(\
               job_id TEXT PRIMARY KEY,state TEXT NOT NULL,argv_json TEXT NOT NULL,cwd TEXT NOT NULL,\
               created_at REAL NOT NULL,started_at REAL,completed_at REAL,pid INTEGER,exit_code INTEGER,\
               timed_out INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,\
               summary TEXT NOT NULL DEFAULT '',evidence_handle TEXT NOT NULL DEFAULT '',\
               error TEXT NOT NULL DEFAULT '',timeout_seconds REAL NOT NULL DEFAULT 0,\
               stdout_path TEXT NOT NULL DEFAULT '',stderr_path TEXT NOT NULL DEFAULT '',\
               repository_tree TEXT NOT NULL DEFAULT 'unknown',environment_hash TEXT NOT NULL DEFAULT 'unknown',\
               project_id TEXT NOT NULL DEFAULT '');\
             CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state,created_at DESC);\
             CREATE TABLE IF NOT EXISTS completion_events(\
               sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,\
               exit_code INTEGER,completed_at REAL NOT NULL,evidence_handle TEXT NOT NULL,payload_json TEXT NOT NULL,\
               FOREIGN KEY(job_id) REFERENCES jobs(job_id));\
             CREATE TABLE IF NOT EXISTS verifier_results(\
               cache_key TEXT PRIMARY KEY,command_json TEXT NOT NULL,tree_hash TEXT NOT NULL,\
               environment_hash TEXT NOT NULL,dependency_hash TEXT NOT NULL,toolchain_hash TEXT NOT NULL,\
               success INTEGER NOT NULL,exit_code INTEGER NOT NULL,evidence_handle TEXT NOT NULL,\
               affected_paths_json TEXT NOT NULL,created_at REAL NOT NULL);\
             INSERT INTO metadata(key,value) VALUES('schema_version','2') \
               ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
        )
        .map_err(|error| format!("INIT_STATE_INITIALIZE_FAILED:{error}"))?;
    let integrity: String = connection
        .query_row("PRAGMA integrity_check", [], |row| row.get(0))
        .map_err(|error| format!("INIT_STATE_INTEGRITY_FAILED:{error}"))?;
    Ok(integrity == "ok")
}

fn initialize_evidence(root: &Path, project_id: &str) -> Result<bool, String> {
    let objects = root.join("objects");
    let metadata = root.join("metadata");
    fs::create_dir_all(&objects)
        .map_err(|error| format!("INIT_EVIDENCE_OBJECTS_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(&metadata)
        .map_err(|error| format!("INIT_EVIDENCE_METADATA_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(root.join("evidence.sqlite3"))
        .map_err(|error| format!("INIT_EVIDENCE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=FULL;\
             CREATE TABLE IF NOT EXISTS evidence_objects(\
               digest TEXT PRIMARY KEY,plaintext_bytes INTEGER NOT NULL,stored_bytes INTEGER NOT NULL,\
               key_version INTEGER NOT NULL,created_at REAL NOT NULL,last_accessed_at REAL NOT NULL,\
               expires_at REAL,ref_count INTEGER NOT NULL DEFAULT 0,legal_hold INTEGER NOT NULL DEFAULT 0);\
             CREATE TABLE IF NOT EXISTS evidence_references(\
               digest TEXT NOT NULL,reference TEXT NOT NULL,created_at REAL NOT NULL,\
               PRIMARY KEY(digest,reference),FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE);",
        )
        .map_err(|error| format!("INIT_EVIDENCE_INITIALIZE_FAILED:{error}"))?;
    let digest = sha256_hex(HEALTH_MARKER);
    let health_root = root.join("health");
    fs::create_dir_all(&health_root)
        .map_err(|error| format!("INIT_EVIDENCE_HEALTH_CREATE_FAILED:{error}"))?;
    let marker = health_root.join(format!("{project_id}-{digest}"));
    fs::write(&marker, HEALTH_MARKER)
        .map_err(|error| format!("INIT_EVIDENCE_HEALTH_WRITE_FAILED:{error}"))?;
    let restored =
        fs::read(&marker).map_err(|error| format!("INIT_EVIDENCE_HEALTH_READ_FAILED:{error}"))?;
    fs::remove_file(marker)
        .map_err(|error| format!("INIT_EVIDENCE_HEALTH_REMOVE_FAILED:{error}"))?;
    Ok(restored == HEALTH_MARKER)
}

fn discover_rollouts(root: &Path) -> Vec<String> {
    fn visit(path: &Path, rows: &mut Vec<(SystemTime, String)>) {
        let Ok(entries) = fs::read_dir(path) else {
            return;
        };
        for entry in entries.flatten() {
            let candidate = entry.path();
            if candidate.is_dir() {
                visit(&candidate, rows);
            } else if candidate.extension().and_then(|value| value.to_str()) == Some("jsonl") {
                let modified = entry
                    .metadata()
                    .and_then(|value| value.modified())
                    .unwrap_or(UNIX_EPOCH);
                rows.push((modified, candidate.to_string_lossy().into_owned()));
            }
        }
    }
    if !root.exists() {
        return Vec::new();
    }
    let mut rows = Vec::new();
    visit(root, &mut rows);
    rows.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| left.1.cmp(&right.1)));
    rows.into_iter().take(5).map(|(_, path)| path).collect()
}

fn git_output(project: &Path, arguments: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(project)
        .args(arguments)
        .output();
    let Ok(output) = output else {
        return "unknown".to_owned();
    };
    if !output.status.success() {
        return "unknown".to_owned();
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if value.is_empty() {
        "".to_owned()
    } else {
        value
    }
}

fn git_identity(project: &Path) -> Value {
    let head = git_output(project, &["rev-parse", "HEAD"]);
    let branch = git_output(project, &["branch", "--show-current"]);
    let tree_hash = git_output(project, &["write-tree"]);
    let status = git_output(project, &["status", "--porcelain"]);
    json!({
        "head": head,
        "branch": branch,
        "tree_hash": tree_hash,
        "dirty": !matches!(status.as_str(), "" | "unknown"),
    })
}

fn canonical_project_id(project: &Path) -> Result<String, String> {
    let canonical = fs::canonicalize(project)
        .map_err(|error| format!("INIT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let value = canonical
        .to_str()
        .ok_or_else(|| "INIT_PROJECT_UTF8_INVALID".to_owned())?;
    #[cfg(windows)]
    let normalized = {
        let mut result = value.replace('/', "\\");
        if let Some(rest) = result.strip_prefix(r"\\?\UNC\") {
            result = format!(r"\\{rest}");
        } else if let Some(rest) = result.strip_prefix(r"\\?\") {
            result = rest.to_owned();
        }
        result.to_lowercase()
    };
    #[cfg(not(windows))]
    let normalized = value.to_owned();
    Ok(sha256_hex(normalized.as_bytes()))
}

fn health(
    project_root: &Path,
    state_root: &Path,
    skill_root: &Path,
    codex_home: &Path,
    host: &str,
    arguments: &[String],
) -> Result<Value, String> {
    let package = repository_root().join("syntavra_runtime");
    let mut checks = Map::new();
    checks.insert(
        "skill_installed".to_owned(),
        Value::Bool(skill_root.join("SKILL.md").is_file()),
    );
    checks.insert(
        "runtime_package".to_owned(),
        Value::Bool(package.join("__init__.py").is_file()),
    );
    for (name, filename) in REQUIRED_MODULES {
        checks.insert(
            (*name).to_owned(),
            Value::Bool(package.join(filename).is_file()),
        );
    }
    checks.insert(
        "state_store".to_owned(),
        Value::Bool(initialize_state(&state_root.join("runtime.sqlite3"))?),
    );
    let project_id = canonical_project_id(project_root)?;
    checks.insert(
        "evidence_store".to_owned(),
        Value::Bool(initialize_evidence(
            &state_root.join("evidence"),
            &project_id,
        )?),
    );
    let negotiation = super::native_host::execute(
        &["host".to_owned(), "negotiate".to_owned()],
        arguments,
        project_root,
    )?;
    checks.insert(
        "host_adapter".to_owned(),
        Value::Bool(negotiation["mode"].as_str() != Some("UNSUPPORTED")),
    );
    let rollouts = discover_rollouts(codex_home);
    checks.insert("rollout_available".to_owned(), Value::Bool(true));

    let mandatory = [
        "skill_installed",
        "runtime_package",
        "state_store",
        "evidence_store",
        "process_broker",
        "output_firewall",
        "context_governor",
        "hook_engine",
        "mcp_server",
        "structural_intelligence",
        "structural_intelligence_v2",
        "host_installer",
        "zero_friction",
        "secure_sandbox",
        "reversible_compression",
        "long_session_runtime",
        "unbounded_context",
        "output_governor",
        "signalbench",
        "paired_benchmark",
        "public_proof",
        "host_adapter",
        "rollout_available",
    ];
    let mut reasons = Vec::<Value>::new();
    for name in mandatory {
        if checks.get(name).and_then(Value::as_bool) != Some(true) {
            reasons.push(Value::String(format!("check-failed:{name}")));
        }
    }
    let skill = checks["skill_installed"].as_bool() == Some(true);
    let runtime = checks["runtime_package"].as_bool() == Some(true);
    let state = checks["state_store"].as_bool() == Some(true);
    let all = reasons.is_empty();
    let state_name = if !skill && !runtime {
        "NOT_INSTALLED"
    } else if skill && !runtime {
        "INSTRUCTION_ONLY"
    } else if all {
        "RUNTIME_ACTIVE"
    } else if runtime && state {
        "RUNTIME_DEGRADED"
    } else {
        "RUNTIME_FAILED"
    };
    Ok(json!({
        "state": state_name,
        "healthy": state_name == "RUNTIME_ACTIVE",
        "checks": checks,
        "reasons": reasons,
        "details": {
            "version": VERSION,
            "release_channel": CHANNEL,
            "host": host,
            "host_negotiation": negotiation,
            "rollout_candidates": rollouts,
            "enforcement_boundary": negotiation["mode"],
            "runtime_plane": "pre-release-001",
        },
    }))
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "INIT_SESSION_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("INIT_SESSION_DIRECTORY_CREATE_FAILED:{error}"))?;
    let temporary = parent.join(format!(
        ".{}.{}",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    let mut bytes = serde_json::to_vec(value)
        .map_err(|error| format!("INIT_SESSION_SERIALIZE_FAILED:{error}"))?;
    bytes.push(b'\n');
    fs::write(&temporary, bytes).map_err(|error| format!("INIT_SESSION_WRITE_FAILED:{error}"))?;
    fs::rename(&temporary, path).map_err(|error| format!("INIT_SESSION_RENAME_FAILED:{error}"))?;
    Ok(())
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("INIT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let task = task(arguments)?;
    let skill_root = option_value(arguments, "--skill-root")?
        .map(PathBuf::from)
        .unwrap_or_else(default_skill_root);
    let codex_home = option_value(arguments, "--codex-home")?
        .map(PathBuf::from)
        .unwrap_or_else(default_codex_home);
    let host = option_value(arguments, "--host")?.unwrap_or_else(|| "codex".to_owned());
    let health = health(
        &project,
        state_root,
        &skill_root,
        &codex_home,
        &host,
        arguments,
    )?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "INIT_SYSTEM_TIME_INVALID".to_owned())?;
    let started_at = now.as_secs_f64();
    let session_id = format!("sc-{}-{}", now.as_secs(), std::process::id());
    let session = json!({
        "schema_version": 3,
        "version": VERSION,
        "release_channel": CHANNEL,
        "session_id": session_id,
        "task": task,
        "project": project.to_string_lossy(),
        "project_id": canonical_project_id(&project)?,
        "git": git_identity(&project),
        "host": host,
        "activation_state": health["state"],
        "started_at": started_at,
    });
    atomic_write_json(
        &state_root
            .join("sessions")
            .join(&session_id)
            .join("session.json"),
        &session,
    )?;
    Ok(json!({
        "session": session,
        "health": health,
    }))
}

#[cfg(test)]
mod tests {
    use super::{supports, task};

    #[test]
    fn routes_init_and_extracts_task() {
        assert!(supports(&["init".to_owned()]));
        let arguments = vec!["init".to_owned(), "index repository".to_owned()];
        assert_eq!(task(&arguments).as_deref(), Ok("index repository"));
    }
}
