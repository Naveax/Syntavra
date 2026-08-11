#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const ACTIONS: &[&str] = &[
    "headless-submit",
    "headless-run",
    "headless-status",
    "headless-events",
    "headless-cancel",
    "headless-resume",
    "headless-export",
    "headless-import",
];

const FINAL_STATES: &[&str] = &["completed", "failed", "cancelled"];

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2 && command[0] == "run" && ACTIONS.contains(&command[1].as_str())
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    let unified = state_root.join("unified");
    fs::create_dir_all(&unified)
        .map_err(|error| format!("HEADLESS_STATE_ROOT_CREATE_FAILED:{error}"))?;
    let database = unified.join("headless.sqlite3");
    initialize(&database)?;
    match command[1].as_str() {
        "headless-submit" => submit(arguments, project, &database).map(Some),
        "headless-run" => run_once(arguments, &database, state_root).map(Some),
        "headless-status" => status(arguments, &database).map(Some),
        "headless-events" => events_action(arguments, &database).map(Some),
        "headless-cancel" => cancel(arguments, &database).map(Some),
        "headless-resume" => resume(arguments, &database).map(Some),
        "headless-export" => export_bundle(arguments, project, &database).map(Some),
        "headless-import" => import_bundle(arguments, project, &database).map(Some),
        _ => Ok(None),
    }
}

fn initialize(path: &Path) -> Result<(), String> {
    let connection =
        Connection::open(path).map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA busy_timeout=30000;\
             CREATE TABLE IF NOT EXISTS jobs(\
               job_id TEXT PRIMARY KEY,state TEXT NOT NULL,workspace_type TEXT NOT NULL,\
               workspace TEXT NOT NULL,command_json TEXT NOT NULL,policy_json TEXT NOT NULL,\
               metadata_json TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL,\
               updated_at TEXT NOT NULL,attempts INTEGER NOT NULL,claimed_by TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS events(\
               sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,event_type TEXT NOT NULL,\
               payload_json TEXT NOT NULL,created_at TEXT NOT NULL,\
               FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE);\
             CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state,created_at);\
             CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id,sequence);",
        )
        .map_err(|error| format!("HEADLESS_DATABASE_INIT_FAILED:{error}"))
}

fn submit(arguments: &[String], project: &Path, database: &Path) -> Result<Value, String> {
    let command_raw = positional_after(arguments, "headless-submit", 0)?;
    let command = load_argv(command_raw, "headless command")?;
    let workspace_raw = option_value(arguments, "--workspace")?.unwrap_or_else(|| ".".to_owned());
    let workspace = project_path(project, &workspace_raw, true)?;
    let workspace_type =
        option_value(arguments, "--workspace-type")?.unwrap_or_else(|| "local-worktree".to_owned());
    let policy = load_object(
        option_value(arguments, "--policy")?
            .as_deref()
            .unwrap_or("{}"),
        "policy",
    )?;
    let metadata = load_object(
        option_value(arguments, "--metadata")?
            .as_deref()
            .unwrap_or("{}"),
        "metadata",
    )?;
    submit_record(
        database,
        &command,
        &workspace,
        &workspace_type,
        &policy,
        &metadata,
        None,
    )
}

fn submit_record(
    database: &Path,
    command: &[String],
    workspace: &Path,
    workspace_type: &str,
    policy: &Value,
    metadata: &Value,
    explicit_job_id: Option<&str>,
) -> Result<Value, String> {
    if command.is_empty() {
        return Err("command is required".to_owned());
    }
    let created = now_iso()?;
    let body = json!({
        "command": command,
        "workspace": workspace.to_string_lossy(),
        "workspace_type": workspace_type,
        "policy": policy,
        "metadata": metadata,
        "created": created,
    });
    let job_id = explicit_job_id.map(str::to_owned).unwrap_or_else(|| {
        format!(
            "sha256:{}",
            sha256_hex(canonical_json(&body).unwrap_or_default().as_bytes())
        )
    });
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO jobs(job_id,state,workspace_type,workspace,command_json,policy_json,metadata_json,result_json,created_at,updated_at,attempts,claimed_by) VALUES(?,?,?,?,?,?,?,?,?,?,0,'')",
            params![
                job_id,
                "queued",
                workspace_type,
                workspace.to_string_lossy(),
                canonical_json(&Value::Array(command.iter().cloned().map(Value::String).collect()))?,
                canonical_json(policy)?,
                canonical_json(metadata)?,
                "{}",
                created,
                created,
            ],
        )
        .map_err(|error| format!("HEADLESS_JOB_INSERT_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            params![job_id, "submitted", canonical_json(&body)?, created],
        )
        .map_err(|error| format!("HEADLESS_EVENT_INSERT_FAILED:{error}"))?;
    get_job(&connection, &job_id)
}

fn status(arguments: &[String], database: &Path) -> Result<Value, String> {
    let values = action_positionals(arguments, "headless-status", &[])?;
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    if let Some(job_id) = values.first() {
        return Ok(json!({"ok": true, "job": get_job(&connection, job_id)?}));
    }
    let total = scalar_i64(&connection, "SELECT COUNT(*) FROM jobs")?;
    let mut states = Map::new();
    let mut statement = connection
        .prepare("SELECT state,COUNT(*) FROM jobs GROUP BY state ORDER BY state")
        .map_err(|error| format!("HEADLESS_STATS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|error| format!("HEADLESS_STATS_QUERY_FAILED:{error}"))?;
    for row in rows {
        let (state, count) = row.map_err(|error| format!("HEADLESS_STATS_ROW_FAILED:{error}"))?;
        states.insert(state, Value::from(count));
    }
    Ok(json!({"ok": true, "jobs": total, "states": states}))
}

fn run_once(arguments: &[String], database: &Path, state_root: &Path) -> Result<Value, String> {
    let worker = option_value(arguments, "--worker")?.unwrap_or_else(|| "local".to_owned());
    if worker.trim().is_empty() {
        return Err("worker identity is required".to_owned());
    }
    let mut connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("HEADLESS_CLAIM_TRANSACTION_FAILED:{error}"))?;
    let job_id = transaction
        .query_row(
            "SELECT job_id FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|error| format!("HEADLESS_CLAIM_QUERY_FAILED:{error}"))?;
    let Some(job_id) = job_id else {
        transaction
            .commit()
            .map_err(|error| format!("HEADLESS_CLAIM_COMMIT_FAILED:{error}"))?;
        return Ok(json!({"ok": true, "job": Value::Null}));
    };
    let claimed_at = now_iso()?;
    transaction
        .execute(
            "UPDATE jobs SET state='claimed',claimed_by=?,updated_at=? WHERE job_id=? AND state='queued'",
            params![worker, claimed_at, job_id],
        )
        .map_err(|error| format!("HEADLESS_CLAIM_UPDATE_FAILED:{error}"))?;
    transaction
        .execute(
            "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            params![
                job_id,
                "claimed",
                canonical_json(&json!({"worker":worker}))?,
                claimed_at
            ],
        )
        .map_err(|error| format!("HEADLESS_CLAIM_EVENT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("HEADLESS_CLAIM_COMMIT_FAILED:{error}"))?;

    transition(database, &job_id, "running", None, Some(&worker), None)?;
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let job = get_job(&connection, &job_id)?;
    let command = job["command"]
        .as_array()
        .ok_or_else(|| "HEADLESS_COMMAND_INVALID".to_owned())?
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let workspace = PathBuf::from(job["workspace"].as_str().unwrap_or_default());
    let policy = job["policy"].clone();
    let sandbox_arguments = sandbox_arguments(&command, &policy)?;
    let sandbox_command = vec!["run".to_owned(), "sandbox-run".to_owned()];
    let execution = super::native_remaining71_sandbox::execute(
        &sandbox_command,
        &sandbox_arguments,
        &workspace,
        state_root,
    );
    let (final_state, result) = match execution {
        Ok(Some(value)) => {
            let ok = value.value["ok"].as_bool().unwrap_or(value.exit_code == 0);
            let mut execution = value.value;
            if let Some(object) = execution.as_object_mut() {
                object.remove("ok");
            }
            (
                if ok { "completed" } else { "failed" },
                json!({"execution": execution}),
            )
        }
        Ok(None) => (
            "failed",
            json!({"error":"RuntimeError: native sandbox route unavailable"}),
        ),
        Err(error) => ("failed", json!({"error":format!("RuntimeError: {error}")})),
    };
    let final_job = transition(database, &job_id, final_state, Some(&result), None, None)?;
    Ok(json!({"ok": true, "job": final_job}))
}

fn sandbox_arguments(command: &[String], policy: &Value) -> Result<Vec<String>, String> {
    let mut output = vec![
        "run".to_owned(),
        "sandbox-run".to_owned(),
        canonical_json(&Value::Array(
            command.iter().cloned().map(Value::String).collect(),
        ))?,
    ];
    if let Some(timeout) = policy.get("timeout_seconds").and_then(Value::as_f64) {
        output.extend(["--timeout".to_owned(), timeout.to_string()]);
    }
    if policy
        .get("strict_native")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        output.push("--strict-native".to_owned());
    }
    if !policy
        .get("allow_child_processes")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        output.push("--no-child-processes".to_owned());
    }
    for host in value_strings(policy.get("network_hosts")) {
        output.extend(["--network-host".to_owned(), host]);
    }
    for path in value_strings(policy.get("writable_paths")) {
        output.extend(["--writable-path".to_owned(), path]);
    }
    if let Some(value) = policy.get("memory_bytes").and_then(Value::as_i64) {
        output.extend(["--memory-bytes".to_owned(), value.to_string()]);
    }
    if let Some(value) = policy.get("cpu_seconds").and_then(Value::as_i64) {
        output.extend(["--cpu-seconds".to_owned(), value.to_string()]);
    }
    Ok(output)
}

fn cancel(arguments: &[String], database: &Path) -> Result<Value, String> {
    let job_id = positional_after(arguments, "headless-cancel", 0)?;
    let reason =
        option_value(arguments, "--reason")?.unwrap_or_else(|| "operator cancellation".to_owned());
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let job = get_job(&connection, job_id)?;
    if job["state"]
        .as_str()
        .is_some_and(|state| FINAL_STATES.contains(&state))
    {
        return Ok(json!({"ok": true, "job": job}));
    }
    let updated = transition(
        database,
        job_id,
        "cancelled",
        Some(&json!({"cancel_reason":reason})),
        None,
        None,
    )?;
    Ok(json!({"ok": true, "job": updated}))
}

fn resume(arguments: &[String], database: &Path) -> Result<Value, String> {
    let job_id = positional_after(arguments, "headless-resume", 0)?;
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let job = get_job(&connection, job_id)?;
    let current = job["state"].as_str().unwrap_or_default();
    if !matches!(current, "blocked" | "failed" | "cancelled") {
        return Err(format!("job cannot be resumed from {current}"));
    }
    let updated = transition(database, job_id, "queued", None, Some(""), Some("resumed"))?;
    Ok(json!({"ok": true, "job": updated}))
}

fn events_action(arguments: &[String], database: &Path) -> Result<Value, String> {
    let job_id = positional_after(arguments, "headless-events", 0)?;
    Ok(json!({"ok": true, "events": events(database, job_id)?}))
}

fn events(database: &Path, job_id: &str) -> Result<Vec<Value>, String> {
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let exists = connection
        .query_row("SELECT 1 FROM jobs WHERE job_id=?", params![job_id], |_| {
            Ok(())
        })
        .optional()
        .map_err(|error| format!("HEADLESS_EVENT_JOB_QUERY_FAILED:{error}"))?
        .is_some();
    if !exists {
        return Err(format!("HEADLESS_JOB_NOT_FOUND:{job_id}"));
    }
    let mut statement = connection
        .prepare("SELECT sequence,job_id,event_type,payload_json,created_at FROM events WHERE job_id=? ORDER BY sequence")
        .map_err(|error| format!("HEADLESS_EVENTS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(params![job_id], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })
        .map_err(|error| format!("HEADLESS_EVENTS_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let (sequence, job, event_type, payload_json, created_at) =
            row.map_err(|error| format!("HEADLESS_EVENTS_ROW_FAILED:{error}"))?;
        output.push(json!({
            "sequence":sequence,
            "job_id":job,
            "event_type":event_type,
            "payload":serde_json::from_str::<Value>(&payload_json).unwrap_or_else(|_|json!({})),
            "created_at":created_at,
        }));
    }
    Ok(output)
}

fn export_bundle(arguments: &[String], project: &Path, database: &Path) -> Result<Value, String> {
    let job_id = positional_after(arguments, "headless-export", 0)?;
    let destination_raw = positional_after(arguments, "headless-export", 1)?;
    let destination = project_path(project, destination_raw, false)?;
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let job = get_job(&connection, job_id)?;
    let payload = json!({
        "schema":"syntavra-headless-job",
        "job":job,
        "events":events(database,job_id)?,
    });
    let digest = sha256_hex(canonical_json(&payload)?.as_bytes());
    let envelope = json!({"sha256":digest,"payload":payload});
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("HEADLESS_EXPORT_PARENT_FAILED:{error}"))?;
    }
    let temporary = destination.with_extension(format!("{}.tmp", std::process::id()));
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&sort_json(&envelope))
            .map_err(|error| format!("HEADLESS_EXPORT_JSON_FAILED:{error}"))?,
    )
    .map_err(|error| format!("HEADLESS_EXPORT_WRITE_FAILED:{error}"))?;
    fs::rename(&temporary, &destination)
        .map_err(|error| format!("HEADLESS_EXPORT_RENAME_FAILED:{error}"))?;
    Ok(json!({"ok":true,"path":destination.to_string_lossy(),"sha256":digest,"job_id":job_id}))
}

fn import_bundle(arguments: &[String], project: &Path, database: &Path) -> Result<Value, String> {
    let source_raw = positional_after(arguments, "headless-import", 0)?;
    let source = project_path(project, source_raw, true)?;
    let envelope = serde_json::from_slice::<Value>(
        &fs::read(&source).map_err(|error| format!("HEADLESS_IMPORT_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("HEADLESS_IMPORT_JSON_INVALID:{error}"))?;
    let payload = envelope
        .get("payload")
        .cloned()
        .ok_or_else(|| "HEADLESS_IMPORT_PAYLOAD_MISSING".to_owned())?;
    let digest = sha256_hex(canonical_json(&payload)?.as_bytes());
    if envelope.get("sha256").and_then(Value::as_str) != Some(digest.as_str()) {
        return Err("headless bundle integrity failure".to_owned());
    }
    let job = payload
        .get("job")
        .ok_or_else(|| "HEADLESS_IMPORT_JOB_MISSING".to_owned())?;
    let command = job["command"]
        .as_array()
        .ok_or_else(|| "HEADLESS_IMPORT_COMMAND_INVALID".to_owned())?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| "HEADLESS_IMPORT_COMMAND_INVALID".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let workspace = match option_value(arguments, "--workspace")? {
        Some(value) => project_path(project, &value, true)?,
        None => PathBuf::from(job["workspace"].as_str().unwrap_or_default()),
    };
    if !workspace.is_dir() {
        return Err("HEADLESS_IMPORT_WORKSPACE_INVALID".to_owned());
    }
    let mut metadata = job["metadata"].clone();
    if !metadata.is_object() {
        metadata = json!({});
    }
    metadata["imported_from"] = Value::String(source.to_string_lossy().into_owned());
    let imported = submit_record(
        database,
        &command,
        &workspace,
        job["workspace_type"].as_str().unwrap_or("imported"),
        &job["policy"],
        &metadata,
        None,
    )?;
    Ok(json!({"ok":true,"job":imported}))
}

fn transition(
    database: &Path,
    job_id: &str,
    target: &str,
    result: Option<&Value>,
    claimed_by: Option<&str>,
    event: Option<&str>,
) -> Result<Value, String> {
    let connection = Connection::open(database)
        .map_err(|error| format!("HEADLESS_DATABASE_OPEN_FAILED:{error}"))?;
    let job = get_job(&connection, job_id)?;
    let current = job["state"].as_str().unwrap_or_default();
    if !transition_allowed(current, target) {
        return Err(format!("invalid job transition: {current} -> {target}"));
    }
    let updated = now_iso()?;
    let attempts = job["attempts"].as_i64().unwrap_or(0) + i64::from(target == "running");
    let mut merged_result = job["result"].clone();
    if !merged_result.is_object() {
        merged_result = json!({});
    }
    if let Some(Value::Object(values)) = result {
        if let Value::Object(existing) = &mut merged_result {
            for (key, value) in values {
                existing.insert(key.clone(), value.clone());
            }
        }
    }
    let owner = claimed_by.unwrap_or_else(|| job["claimed_by"].as_str().unwrap_or_default());
    connection
        .execute(
            "UPDATE jobs SET state=?,result_json=?,updated_at=?,attempts=?,claimed_by=? WHERE job_id=?",
            params![target,canonical_json(&merged_result)?,updated,attempts,owner,job_id],
        )
        .map_err(|error| format!("HEADLESS_TRANSITION_UPDATE_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            params![
                job_id,
                event.unwrap_or(target),
                canonical_json(&json!({"from":current,"to":target,"result":result.cloned().unwrap_or_else(||json!({}))}))?,
                updated,
            ],
        )
        .map_err(|error| format!("HEADLESS_TRANSITION_EVENT_FAILED:{error}"))?;
    get_job(&connection, job_id)
}

fn transition_allowed(current: &str, target: &str) -> bool {
    match current {
        "queued" => matches!(target, "claimed" | "cancelled"),
        "claimed" => matches!(target, "running" | "queued" | "cancelled"),
        "running" => matches!(
            target,
            "verifying" | "completed" | "failed" | "blocked" | "cancelled"
        ),
        "verifying" => matches!(target, "completed" | "failed" | "blocked" | "cancelled"),
        "blocked" => matches!(target, "queued" | "cancelled"),
        "failed" | "cancelled" => target == "queued",
        "completed" => false,
        _ => false,
    }
}

fn get_job(connection: &Connection, job_id: &str) -> Result<Value, String> {
    let value = connection
        .query_row(
            "SELECT job_id,state,workspace_type,workspace,command_json,policy_json,metadata_json,result_json,created_at,updated_at,attempts,claimed_by FROM jobs WHERE job_id=?",
            params![job_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, String>(11)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("HEADLESS_JOB_QUERY_FAILED:{error}"))?;
    let Some((
        id,
        state,
        workspace_type,
        workspace,
        command_json,
        policy_json,
        metadata_json,
        result_json,
        created_at,
        updated_at,
        attempts,
        claimed_by,
    )) = value
    else {
        return Err(format!("HEADLESS_JOB_NOT_FOUND:{job_id}"));
    };
    Ok(json!({
        "job_id":id,
        "state":state,
        "workspace_type":workspace_type,
        "workspace":workspace,
        "command":serde_json::from_str::<Value>(&command_json).unwrap_or_else(|_|json!([])),
        "policy":serde_json::from_str::<Value>(&policy_json).unwrap_or_else(|_|json!({})),
        "metadata":serde_json::from_str::<Value>(&metadata_json).unwrap_or_else(|_|json!({})),
        "created_at":created_at,
        "updated_at":updated_at,
        "attempts":attempts,
        "claimed_by":claimed_by,
        "result":serde_json::from_str::<Value>(&result_json).unwrap_or_else(|_|json!({})),
    }))
}

fn scalar_i64(connection: &Connection, sql: &str) -> Result<i64, String> {
    connection
        .query_row(sql, [], |row| row.get::<_, i64>(0))
        .map_err(|error| format!("HEADLESS_SCALAR_QUERY_FAILED:{error}"))
}

fn load_argv(value: &str, label: &str) -> Result<Vec<String>, String> {
    let value = load_json(value)?;
    let rows = value
        .as_array()
        .ok_or_else(|| format!("{label} must be a non-empty JSON argv list"))?;
    if rows.is_empty() {
        return Err(format!("{label} must be a non-empty JSON argv list"));
    }
    rows.iter()
        .map(|row| {
            row.as_str()
                .filter(|item| !item.contains('\0'))
                .map(str::to_owned)
                .ok_or_else(|| format!("{label} must be a non-empty JSON argv list"))
        })
        .collect()
}

fn load_object(value: &str, label: &str) -> Result<Value, String> {
    let value = load_json(value)?;
    if !value.is_object() {
        return Err(format!("{label} must be a JSON object"));
    }
    Ok(value)
}

fn load_json(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value).map_err(|error| format!("HEADLESS_JSON_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("HEADLESS_JSON_INVALID:{error}"))
}

fn value_strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn project_path(project: &Path, value: &str, must_exist: bool) -> Result<PathBuf, String> {
    let root = fs::canonicalize(project)
        .map_err(|error| format!("HEADLESS_PROJECT_RESOLVE_FAILED:{error}"))?;
    let candidate = Path::new(value);
    let joined = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    let resolved = if must_exist {
        fs::canonicalize(&joined)
            .map_err(|error| format!("HEADLESS_PATH_RESOLVE_FAILED:{error}"))?
    } else {
        joined
    };
    if !resolved.starts_with(&root) {
        return Err("HEADLESS_PATH_ESCAPES_PROJECT".to_owned());
    }
    Ok(resolved)
}

fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(value))
        .map_err(|error| format!("HEADLESS_JSON_SERIALIZE_FAILED:{error}"))
}

fn sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sort_json(&map[key]));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(sort_json).collect()),
        _ => value.clone(),
    }
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted / 146_097
    } else {
        (shifted - 146_096) / 146_097
    };
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month, day)
}

fn format_utc_timestamp(seconds: i64, micros: u32) -> Result<String, String> {
    if micros >= 1_000_000 {
        return Err("HEADLESS_CLOCK_MICROS_INVALID".to_owned());
    }
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    if !(0..=9_999).contains(&year) {
        return Err("HEADLESS_CLOCK_YEAR_OUT_OF_RANGE".to_owned());
    }
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00"
    ))
}

fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("HEADLESS_CLOCK_FAILED:{error}"))?;
    let seconds = i64::try_from(duration.as_secs())
        .map_err(|_| "HEADLESS_CLOCK_SECONDS_OUT_OF_RANGE".to_owned())?;
    format_utc_timestamp(seconds, duration.subsec_micros())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut output = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let current = &arguments[index];
        let found = if current == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            current
                .strip_prefix(flag)
                .and_then(|tail| tail.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(found) = found {
            if output.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            output = Some(found);
        }
        index += 1;
    }
    Ok(output)
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    position: usize,
) -> Result<&'a str, String> {
    action_positionals(
        arguments,
        action,
        &[
            "--workspace",
            "--workspace-type",
            "--policy",
            "--metadata",
            "--worker",
            "--reason",
        ],
    )?
    .get(position)
    .copied()
    .ok_or_else(|| format!("HEADLESS_POSITIONAL_MISSING:{action}:{position}"))
}

fn action_positionals<'a>(
    arguments: &'a [String],
    action: &str,
    value_flags: &[&str],
) -> Result<Vec<&'a str>, String> {
    let mut index = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|value| value + 2)
        .ok_or_else(|| format!("HEADLESS_ACTION_NOT_FOUND:{action}"))?;
    let mut values = Vec::new();
    while index < arguments.len() {
        if value_flags.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        values.push(arguments[index].as_str());
        index += 1;
    }
    Ok(values)
}
