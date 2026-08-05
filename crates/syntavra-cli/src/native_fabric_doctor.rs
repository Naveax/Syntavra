#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use rusqlite::{params, Connection};
use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "doctor")
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            index += 1;
            found = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{name}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

#[cfg(windows)]
fn candidate_names(name: &str) -> Vec<String> {
    if Path::new(name).extension().is_some() {
        return vec![name.to_owned()];
    }
    env::var("PATHEXT")
        .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_owned())
        .split(';')
        .filter(|value| !value.is_empty())
        .map(|extension| format!("{name}{extension}"))
        .collect()
}

#[cfg(not(windows))]
fn candidate_names(name: &str) -> Vec<String> {
    vec![name.to_owned()]
}

fn executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(windows)]
    {
        true
    }
}

fn which(name: &str) -> Option<String> {
    let search = env::var_os("PATH").unwrap_or_default();
    for directory in env::split_paths(&search) {
        for candidate in candidate_names(name) {
            let path = directory.join(candidate);
            if executable(&path) {
                return Some(path.to_string_lossy().into_owned());
            }
        }
    }
    None
}

fn initialize_database(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("FABRIC_DOCTOR_STATE_CREATE_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("FABRIC_DOCTOR_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .busy_timeout(std::time::Duration::from_secs(30))
        .map_err(|error| format!("FABRIC_DOCTOR_BUSY_TIMEOUT_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS jobs(\
                job_id TEXT PRIMARY KEY,\
                state TEXT NOT NULL,\
                argv_json TEXT NOT NULL,\
                cwd TEXT NOT NULL,\
                created_at REAL NOT NULL,\
                started_at REAL,\
                completed_at REAL,\
                pid INTEGER,\
                exit_code INTEGER,\
                timed_out INTEGER NOT NULL DEFAULT 0,\
                cancelled INTEGER NOT NULL DEFAULT 0,\
                summary TEXT NOT NULL DEFAULT '',\
                evidence_handle TEXT NOT NULL DEFAULT '',\
                error TEXT NOT NULL DEFAULT '',\
                timeout_seconds REAL NOT NULL DEFAULT 0,\
                stdout_path TEXT NOT NULL DEFAULT '',\
                stderr_path TEXT NOT NULL DEFAULT '',\
                repository_tree TEXT NOT NULL DEFAULT 'unknown',\
                environment_hash TEXT NOT NULL DEFAULT 'unknown',\
                project_id TEXT NOT NULL DEFAULT ''\
             );\
             CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at DESC);\
             CREATE TABLE IF NOT EXISTS completion_events(\
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,\
                job_id TEXT NOT NULL UNIQUE,\
                state TEXT NOT NULL,\
                exit_code INTEGER,\
                completed_at REAL NOT NULL,\
                evidence_handle TEXT NOT NULL,\
                payload_json TEXT NOT NULL,\
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)\
             );\
             CREATE TABLE IF NOT EXISTS verifier_results(\
                cache_key TEXT PRIMARY KEY,\
                command_json TEXT NOT NULL,\
                tree_hash TEXT NOT NULL,\
                environment_hash TEXT NOT NULL,\
                dependency_hash TEXT NOT NULL,\
                toolchain_hash TEXT NOT NULL,\
                success INTEGER NOT NULL,\
                exit_code INTEGER NOT NULL,\
                evidence_handle TEXT NOT NULL,\
                affected_paths_json TEXT NOT NULL,\
                created_at REAL NOT NULL\
             );\
             CREATE TABLE IF NOT EXISTS fabric_events(\
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
        .map_err(|error| format!("FABRIC_DOCTOR_SCHEMA_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version',?) \
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params!["2"],
        )
        .map_err(|error| format!("FABRIC_DOCTOR_SCHEMA_VERSION_FAILED:{error}"))?;
    Ok(connection)
}

fn integrity(connection: &Connection) -> Result<bool, String> {
    let value = connection
        .query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
        .map_err(|error| format!("FABRIC_DOCTOR_INTEGRITY_FAILED:{error}"))?;
    Ok(value == "ok")
}

fn write_output(path: &Path, value: &Value) -> Result<Value, String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_DOCTOR_OUTPUT_PARENT_FAILED:{error}"))?;
        }
    }
    let rendered = serde_json::to_string_pretty(value)
        .map_err(|error| format!("FABRIC_DOCTOR_OUTPUT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(path, rendered.as_bytes())
        .map_err(|error| format!("FABRIC_DOCTOR_OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "output": path.to_string_lossy(),
        "bytes": rendered.len(),
    }))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let host = option_value(arguments, "--host")?.unwrap_or_else(|| "codex".to_owned());
    let database_path = state_root.join("competitive-fabric.sqlite3");
    let database = initialize_database(&database_path)?;
    let host_contract = super::native_host::doctor_contract(&host);

    let docker = which("docker");
    let podman = which("podman");
    let bwrap = if cfg!(windows) { None } else { which("bwrap") };
    let strict_sandbox_available = docker.is_some() || podman.is_some() || bwrap.is_some();

    let analytics_database = integrity(&database)?;
    let project_exists = project_root.exists();
    let known_host = host_contract["known_host"].as_bool().unwrap_or(false);
    let mcp_available = host_contract["mcp_available"].as_bool().unwrap_or(false);
    let result_replacement = host_contract["result_replacement"]
        .as_bool()
        .unwrap_or(false);
    let enforced_mode = host_contract["enforced_mode"].as_bool().unwrap_or(false);
    let platform_registry_size = host_contract["platform_registry_size"]
        .as_u64()
        .unwrap_or(0);

    let ok = analytics_database
        && project_exists
        && known_host
        && mcp_available
        && enforced_mode
        && platform_registry_size != 0;
    let mut limitations = Vec::new();
    if !result_replacement {
        limitations.push("result_replacement");
    }
    if !enforced_mode {
        limitations.push("enforced_mode");
    }
    if !strict_sandbox_available {
        limitations.push("strict_sandbox_available");
    }

    let value = json!({
        "ok": ok,
        "host": host,
        "checks": {
            "analytics_database": analytics_database,
            "project_exists": project_exists,
            "known_host": known_host,
            "mcp_available": mcp_available,
            "result_replacement": result_replacement,
            "enforced_mode": enforced_mode,
            "strict_sandbox_available": strict_sandbox_available,
            "platform_registry_size": platform_registry_size,
        },
        "sandbox_backends": {
            "docker": docker,
            "podman": podman,
            "bwrap": bwrap,
        },
        "negotiation": host_contract["negotiation"].clone(),
        "profile_names": ["audit", "balanced", "full", "minimal", "optimized", "tiny"],
        "limitations": limitations,
    });

    option_value(arguments, "--output")?.map_or_else(
        || Ok(value.clone()),
        |path| write_output(&PathBuf::from(path), &value),
    )
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_fabric_doctor_only() {
        assert!(supports(&["fabric".to_owned(), "doctor".to_owned()]));
        assert!(!supports(&["doctor".to_owned()]));
    }
}
