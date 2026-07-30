#![forbid(unsafe_code)]
#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::missing_errors_doc,
    clippy::module_name_repetitions,
    clippy::too_many_lines
)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{ErrorKind, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

use crate::config_contract::{resolve_config_wire, snapshot_json};
use crate::config_last_good_apply::apply_json as apply_last_good_json;
use crate::state_snapshot_contract::project_id_for_root;

const SCHEMA_VERSION: u64 = 1;
const CONTRACT_VERSION: u64 = 1;
const RUNTIME_ID: &str = "syntavra-full-parity-runtime-v1";
const STATE_RELATIVE: &str = ".syntavra/pre-release/full-parity";
const MAX_REQUEST_BYTES: usize = 1024 * 1024;
const MAX_TEXT_BYTES: usize = 256 * 1024;
const MAX_NETWORK_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullParityFailure {
    pub code: String,
}

impl FullParityFailure {
    fn new(code: impl Into<String>) -> Self {
        Self { code: code.into() }
    }
}

type Result<T> = std::result::Result<T, FullParityFailure>;
type Mutation = BTreeMap<&'static str, bool>;

fn failure<T>(code: &str) -> Result<T> {
    Err(FullParityFailure::new(code))
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| FullParityFailure::new("FULL_PARITY_JSON_RENDER_FAILED"))
}

fn digest(value: &[u8]) -> String {
    sha256_hex(value)
}

fn mutation(database: bool, filesystem: bool, host: bool, network: bool, process: bool) -> Mutation {
    BTreeMap::from([
        ("database", database),
        ("filesystem", filesystem),
        ("host", host),
        ("network", network),
        ("process", process),
    ])
}

fn reject_symlink(path: &Path, code: &str) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => failure(code),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(_) => failure(code),
    }
}

fn project_root(project_root: &str, expected_project_id: &str) -> Result<PathBuf> {
    if !is_lower_hash(expected_project_id) {
        return failure("FULL_PARITY_EXPECTED_PROJECT_INVALID");
    }
    let actual = project_id_for_root(project_root)
        .map_err(|code| FullParityFailure::new(format!("FULL_PARITY_{}", code.trim_start_matches("STATE_"))))?;
    if actual != expected_project_id {
        return failure("FULL_PARITY_PROJECT_MISMATCH");
    }
    let root = fs::canonicalize(project_root)
        .map_err(|_| FullParityFailure::new("FULL_PARITY_PROJECT_ROOT_CHANGED"))?;
    reject_symlink(&root, "FULL_PARITY_PROJECT_ROOT_SYMLINK")?;
    Ok(root)
}

fn state_root(root: &Path) -> Result<PathBuf> {
    let state = root.join(STATE_RELATIVE);
    let mut current = root.to_path_buf();
    for part in Path::new(STATE_RELATIVE).components() {
        let Component::Normal(part) = part else {
            return failure("FULL_PARITY_STATE_PATH_INVALID");
        };
        current.push(part);
        reject_symlink(&current, "FULL_PARITY_STATE_SYMLINK")?;
    }
    fs::create_dir_all(&state).map_err(|_| FullParityFailure::new("FULL_PARITY_STATE_CREATE_FAILED"))?;
    reject_symlink(&state, "FULL_PARITY_STATE_SYMLINK")?;
    Ok(state)
}

fn set_private_mode(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(metadata) = fs::metadata(path) {
            let mut permissions = metadata.permissions();
            permissions.set_mode(0o600);
            let _ = fs::set_permissions(path, permissions);
        }
    }
}

fn atomic_write(path: &Path, payload: &[u8]) -> Result<()> {
    reject_symlink(path, "FULL_PARITY_TARGET_SYMLINK")?;
    let parent = path.parent().ok_or_else(|| FullParityFailure::new("FULL_PARITY_TARGET_PARENT_INVALID"))?;
    fs::create_dir_all(parent).map_err(|_| FullParityFailure::new("FULL_PARITY_ATOMIC_WRITE_FAILED"))?;
    reject_symlink(parent, "FULL_PARITY_TARGET_PARENT_SYMLINK")?;
    let name = path.file_name().and_then(|value| value.to_str()).ok_or_else(|| FullParityFailure::new("FULL_PARITY_TARGET_NAME_INVALID"))?;
    let temporary = parent.join(format!(".{name}.tmp"));
    reject_symlink(&temporary, "FULL_PARITY_TEMP_SYMLINK")?;
    let result = (|| -> std::io::Result<()> {
        let mut file = File::create(&temporary)?;
        file.write_all(payload)?;
        file.flush()?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
        return failure("FULL_PARITY_ATOMIC_WRITE_FAILED");
    }
    set_private_mode(path);
    Ok(())
}

fn read_json(path: &Path, default: Value) -> Result<Value> {
    reject_symlink(path, "FULL_PARITY_TARGET_SYMLINK")?;
    if !path.exists() {
        return Ok(default);
    }
    let raw = fs::read(path).map_err(|_| FullParityFailure::new("FULL_PARITY_STATE_READ_FAILED"))?;
    if raw.len() > MAX_REQUEST_BYTES {
        return failure("FULL_PARITY_STATE_TOO_LARGE");
    }
    serde_json::from_slice(&raw).map_err(|_| FullParityFailure::new("FULL_PARITY_STATE_JSON_INVALID"))
}

fn write_json(path: &Path, value: &Value) -> Result<()> {
    let mut payload = canonical_bytes(value)?;
    payload.push(b'\n');
    atomic_write(path, &payload)
}

fn object(value: &Value, code: &str) -> Result<&Map<String, Value>> {
    value.as_object().ok_or_else(|| FullParityFailure::new(code))
}

fn string<'a>(payload: &'a Map<String, Value>, key: &str, maximum: usize) -> Result<&'a str> {
    let value = payload.get(key).and_then(Value::as_str).ok_or_else(|| FullParityFailure::new(format!("FULL_PARITY_{}_INVALID", key.to_uppercase())))?;
    if value.len() > maximum {
        return failure(&format!("FULL_PARITY_{}_INVALID", key.to_uppercase()));
    }
    Ok(value)
}

fn integer(payload: &Map<String, Value>, key: &str, minimum: u64, maximum: u64) -> Result<u64> {
    let value = payload.get(key).and_then(Value::as_u64).ok_or_else(|| FullParityFailure::new(format!("FULL_PARITY_{}_INVALID", key.to_uppercase())))?;
    if value < minimum || value > maximum {
        return failure(&format!("FULL_PARITY_{}_INVALID", key.to_uppercase()));
    }
    Ok(value)
}

fn is_lower_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_identifier(value: &str, maximum: usize) -> bool {
    !value.is_empty()
        && value.len() <= maximum
        && value.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._:-".contains(&byte))
        && value.as_bytes()[0].is_ascii_alphanumeric()
}

fn is_profile_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._-".contains(&byte))
        && value.as_bytes()[0].is_ascii_alphanumeric()
}

fn decode_hex(value: &str, code: &str) -> Result<Vec<u8>> {
    if value.len() % 2 != 0 {
        return failure(code);
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = hex_nibble(pair[0]).ok_or_else(|| FullParityFailure::new(code))?;
        let low = hex_nibble(pair[1]).ok_or_else(|| FullParityFailure::new(code))?;
        output.push((high << 4) | low);
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn encode_hex(value: &[u8]) -> String {
    const TABLE: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(char::from(TABLE[usize::from(byte >> 4)]));
        output.push(char::from(TABLE[usize::from(byte & 0x0f)]));
    }
    output
}

fn safe_relative(value: &str) -> Result<PathBuf> {
    if value.is_empty() || value.as_bytes().contains(&0) || value.len() > 512 {
        return failure("FULL_PARITY_PATH_INVALID");
    }
    let normalized = value.replace('\\', "/");
    let path = Path::new(&normalized);
    let mut count = 0usize;
    for part in path.components() {
        match part {
            Component::Normal(value) if !value.is_empty() => count += 1,
            _ => return failure("FULL_PARITY_PATH_INVALID"),
        }
    }
    if count == 0 || count > 16 {
        return failure("FULL_PARITY_PATH_INVALID");
    }
    Ok(path.to_path_buf())
}

fn receipt(phase: &str, operation: &str, project_id: &str, request: &Value, result: &Value) -> Result<Value> {
    let body = json!({
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "phase": phase,
        "project_id": project_id,
        "request_sha256": digest(&canonical_bytes(request)?),
        "result_sha256": digest(&canonical_bytes(result)?),
        "schema_version": SCHEMA_VERSION,
    });
    let receipt_hash = digest(&canonical_bytes(&body)?);
    let mut map = body.as_object().cloned().ok_or_else(|| FullParityFailure::new("FULL_PARITY_RECEIPT_INVALID"))?;
    map.insert("receipt_hash".to_owned(), Value::String(receipt_hash));
    Ok(Value::Object(map))
}

fn envelope(phase: &str, operation: &str, project_id: &str, request: &Value, result: Value, mutation: Mutation) -> Result<String> {
    let value = json!({
        "claim": "R25_R37_FULL_PARITY_PROVEN",
        "contract_version": CONTRACT_VERSION,
        "mutation": mutation,
        "ok": true,
        "operation": operation,
        "phase": phase,
        "project_id": project_id,
        "receipt": receipt(phase, operation, project_id, request, &result)?,
        "result": result,
        "runtime_id": RUNTIME_ID,
        "schema_version": SCHEMA_VERSION,
    });
    serde_json::to_string(&value).map_err(|_| FullParityFailure::new("FULL_PARITY_JSON_RENDER_FAILED"))
}

fn profile_state(state: &Path) -> Result<Value> {
    let value = read_json(&state.join("profiles.json"), json!({"profiles": {}, "schema_version": 1, "selected": null}))?;
    let row = object(&value, "PROFILE_STATE_INVALID")?;
    if row.get("schema_version").and_then(Value::as_u64) != Some(1)
        || !row.get("profiles").is_some_and(Value::is_object)
        || !row.get("selected").is_some_and(|item| item.is_null() || item.is_string())
    {
        return failure("PROFILE_STATE_INVALID");
    }
    Ok(value)
}

fn profile_result(value: &Value) -> Result<Value> {
    let row = object(value, "PROFILE_STATE_INVALID")?;
    let profiles = object(row.get("profiles").ok_or_else(|| FullParityFailure::new("PROFILE_STATE_INVALID"))?, "PROFILE_STATE_INVALID")?;
    let mut rendered = Vec::with_capacity(profiles.len());
    for (name, profile) in profiles {
        let profile = object(profile, "PROFILE_STATE_INVALID")?;
        rendered.push(json!({
            "config_hash": profile.get("config_hash").and_then(Value::as_str).ok_or_else(|| FullParityFailure::new("PROFILE_STATE_INVALID"))?,
            "metadata": profile.get("metadata").cloned().unwrap_or_else(|| json!({})),
            "name": name,
        }));
    }
    Ok(json!({"profiles": rendered, "selected": row.get("selected").cloned().unwrap_or(Value::Null)}))
}

fn profile_config(payload: &Map<String, Value>) -> Result<(String, String, Vec<u8>)> {
    let wire_hex = string(payload, "config_wire_hex", MAX_REQUEST_BYTES * 2)?;
    let wire = decode_hex(wire_hex, "PROFILE_CONFIG_WIRE_INVALID")?;
    let snapshot = resolve_config_wire(&wire).map_err(FullParityFailure::new)?;
    Ok((wire_hex.to_ascii_lowercase(), snapshot.config_hash, wire))
}

fn profile_apply(root: &Path, project_id: &str, wire: &[u8]) -> Result<Value> {
    let rendered = apply_last_good_json(
        root.to_str().ok_or_else(|| FullParityFailure::new("FULL_PARITY_PROJECT_ROOT_UTF8_INVALID"))?,
        project_id,
        wire,
        None,
        Some(1_800_000_000),
    )
    .map_err(|error| FullParityFailure::new(error.code))?;
    serde_json::from_str(&rendered).map_err(|_| FullParityFailure::new("PROFILE_LAST_GOOD_APPLY_INVALID"))
}

fn phase_r25(operation: &str, payload: &Map<String, Value>, root: &Path, project_id: &str) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    let mut value = profile_state(&state)?;
    if operation == "profile.list" {
        return Ok((profile_result(&value)?, mutation(false, false, false, false, false)));
    }
    let name = string(payload, "name", 64)?;
    if !is_profile_name(name) {
        return failure("PROFILE_NAME_INVALID");
    }
    let selected_before = value.get("selected").and_then(Value::as_str) == Some(name);
    let profiles = value.get_mut("profiles").and_then(Value::as_object_mut).ok_or_else(|| FullParityFailure::new("PROFILE_STATE_INVALID"))?;
    let mut last_good = None;
    match operation {
        "profile.create" | "profile.update" => {
            let exists = profiles.contains_key(name);
            if operation == "profile.create" && exists {
                return failure("PROFILE_ALREADY_EXISTS");
            }
            if operation == "profile.update" && !exists {
                return failure("PROFILE_NOT_FOUND");
            }
            let (wire_hex, config_hash, wire) = profile_config(payload)?;
            let metadata = payload.get("metadata").cloned().unwrap_or_else(|| json!({}));
            if !metadata.is_object() || canonical_bytes(&metadata)?.len() > 16_384 {
                return failure("PROFILE_METADATA_INVALID");
            }
            profiles.insert(name.to_owned(), json!({"config_hash": config_hash, "config_wire_hex": wire_hex, "metadata": metadata}));
            if payload.get("select").and_then(Value::as_bool).unwrap_or(false) || selected_before {
                value["selected"] = Value::String(name.to_owned());
                last_good = Some(profile_apply(root, project_id, &wire)?);
            }
        }
        "profile.select" => {
            let profile = profiles.get(name).and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("PROFILE_NOT_FOUND"))?;
            let wire_hex = profile.get("config_wire_hex").and_then(Value::as_str).ok_or_else(|| FullParityFailure::new("PROFILE_STATE_INVALID"))?;
            let wire = decode_hex(wire_hex, "PROFILE_CONFIG_WIRE_INVALID")?;
            value["selected"] = Value::String(name.to_owned());
            last_good = Some(profile_apply(root, project_id, &wire)?);
        }
        "profile.delete" => {
            if profiles.remove(name).is_none() {
                return failure("PROFILE_NOT_FOUND");
            }
            if selected_before {
                value["selected"] = Value::Null;
            }
        }
        _ => return failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
    write_json(&state.join("profiles.json"), &value)?;
    let mut result = profile_result(&value)?;
    let last_good_value = if let Some(last_good) = last_good {
        let result = last_good.get("result").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("PROFILE_LAST_GOOD_APPLY_INVALID"))?;
        json!({
            "action": result.get("action").cloned().unwrap_or(Value::Null),
            "config_hash": result.get("config_hash").cloned().unwrap_or(Value::Null),
            "stored_payload_sha256": result.get("stored_payload_sha256").cloned().unwrap_or(Value::Null),
        })
    } else {
        Value::Null
    };
    result["last_good"] = last_good_value;
    Ok((result, mutation(false, true, false, false, false)))
}

fn receipt_wire(engine: &str, operation: &str, created_at_ms: u64, project_id: &str, receipt_id: &str, payload_hash: &str, previous_hash: Option<&str>) -> Result<Vec<u8>> {
    if !matches!(engine, "python" | "rust") {
        return failure("RECEIPT_ENGINE_INVALID");
    }
    if !is_identifier(operation, 128) || !is_identifier(receipt_id, 128) {
        return failure("RECEIPT_IDENTIFIER_INVALID");
    }
    if !is_lower_hash(payload_hash) || previous_hash.is_some_and(|value| !is_lower_hash(value)) {
        return failure("RECEIPT_PAYLOAD_HASH_INVALID");
    }
    let lines = [
        "R7RCPT1".to_owned(),
        "schema_version=1".to_owned(),
        "product_version=0.0.1".to_owned(),
        "contract_version=1".to_owned(),
        format!("engine={engine}"),
        format!("operation_hex={}", encode_hex(operation.as_bytes())),
        format!("created_at_ms={created_at_ms}"),
        format!("project_id={project_id}"),
        format!("receipt_id_hex={}", encode_hex(receipt_id.as_bytes())),
        format!("payload_hash={payload_hash}"),
        format!("previous_hash={}", previous_hash.unwrap_or("-")),
        "fallback_from=-".to_owned(),
        "fallback_to=-".to_owned(),
        "fallback_reason_hex=".to_owned(),
        "fallback_state_mutated=false".to_owned(),
    ];
    let mut material = format!("{}\n", lines.join("\n")).into_bytes();
    let receipt_hash = digest(&material);
    material.extend_from_slice(format!("receipt_hash={receipt_hash}\n").as_bytes());
    Ok(material)
}

fn phase_r26(operation: &str, payload: &Map<String, Value>, root: &Path, project_id: &str) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    match operation {
        "state.write" => {
            let target_id = string(payload, "target", 64)?;
            let target = match target_id {
                "project-config" => root.join(".syntavra/config.toml"),
                "engine-selection" => root.join(".syntavra/engine.json"),
                "runtime-marker" => state.join("runtime-marker.json"),
                _ => return failure("STATE_WRITE_TARGET_INVALID"),
            };
            let content = decode_hex(string(payload, "content_hex", MAX_REQUEST_BYTES * 2)?, "STATE_WRITE_CONTENT_INVALID")?;
            if content.len() > MAX_REQUEST_BYTES {
                return failure("STATE_WRITE_CONTENT_TOO_LARGE");
            }
            atomic_write(&target, &content)?;
            Ok((json!({"bytes": content.len(), "sha256": digest(&content), "target": target_id}), mutation(false, true, false, false, false)))
        }
        "receipt.write" => {
            let receipt_id = string(payload, "receipt_id", 128)?;
            let receipt_operation = string(payload, "receipt_operation", 128)?;
            let payload_hash = string(payload, "payload_hash", 64)?;
            let previous = payload.get("previous_hash").and_then(Value::as_str);
            if payload.get("previous_hash").is_some_and(|value| !value.is_null() && !value.is_string()) {
                return failure("RECEIPT_PREVIOUS_HASH_INVALID");
            }
            let created_at_ms = integer(payload, "created_at_ms", 0, i64::MAX as u64)?;
            let engine = payload.get("engine").and_then(Value::as_str).unwrap_or("python");
            let wire = receipt_wire(engine, receipt_operation, created_at_ms, project_id, receipt_id, payload_hash, previous)?;
            let target = state.join("receipts").join(format!("{receipt_id}.receipt"));
            atomic_write(&target, &wire)?;
            let last = std::str::from_utf8(&wire).ok().and_then(|text| text.lines().last()).and_then(|line| line.split_once('=')).map_or("", |(_, value)| value);
            Ok((json!({"bytes": wire.len(), "receipt_hash": last, "receipt_id": receipt_id, "wire_hex": encode_hex(&wire)}), mutation(false, true, false, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn broker_connection(state: &Path) -> Result<Connection> {
    let connection = Connection::open(state.join("broker.sqlite3"))
        .map_err(|_| FullParityFailure::new("BROKER_DATABASE_OPEN_FAILED"))?;
    connection
        .execute_batch(
            "PRAGMA foreign_keys=ON; PRAGMA journal_mode=DELETE;\
             CREATE TABLE IF NOT EXISTS jobs(\
             job_id TEXT PRIMARY KEY, argv_json TEXT NOT NULL, priority INTEGER NOT NULL,\
             state TEXT NOT NULL, worker TEXT, exit_code INTEGER, stdout_hash TEXT);",
        )
        .map_err(|_| FullParityFailure::new("BROKER_DATABASE_INIT_FAILED"))?;
    Ok(connection)
}

fn broker_rows(connection: &Connection) -> Result<Value> {
    let mut statement = connection
        .prepare(
            "SELECT job_id,argv_json,priority,state,worker,exit_code,stdout_hash \
             FROM jobs ORDER BY priority DESC, job_id ASC",
        )
        .map_err(|_| FullParityFailure::new("BROKER_QUERY_FAILED"))?;
    let rows = statement
        .query_map([], |row| {
            let argv_json: String = row.get(1)?;
            let argv: Value = serde_json::from_str(&argv_json).map_err(|error| {
                rusqlite::Error::FromSqlConversionFailure(
                    1,
                    rusqlite::types::Type::Text,
                    Box::new(error),
                )
            })?;
            Ok(json!({
                "argv": argv,
                "exit_code": row.get::<_, Option<i64>>(5)?,
                "job_id": row.get::<_, String>(0)?,
                "priority": row.get::<_, i64>(2)?,
                "state": row.get::<_, String>(3)?,
                "stdout_hash": row.get::<_, Option<String>>(6)?,
                "worker": row.get::<_, Option<String>>(4)?,
            }))
        })
        .map_err(|_| FullParityFailure::new("BROKER_QUERY_FAILED"))?;
    let values = rows
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|_| FullParityFailure::new("BROKER_QUERY_FAILED"))?;
    Ok(Value::Array(values))
}

fn phase_r27(operation: &str, payload: &Map<String, Value>, root: &Path) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    let connection = broker_connection(&state)?;
    match operation {
        "broker.enqueue" => {
            let job_id = string(payload, "job_id", 128)?;
            if !is_identifier(job_id, 128) {
                return failure("BROKER_JOB_ID_INVALID");
            }
            let argv = payload
                .get("argv")
                .and_then(Value::as_array)
                .ok_or_else(|| FullParityFailure::new("BROKER_ARGV_INVALID"))?;
            if argv.is_empty()
                || argv.len() > 64
                || argv.iter().any(|value| !value.is_string())
            {
                return failure("BROKER_ARGV_INVALID");
            }
            let priority = integer(payload, "priority", 0, 1000)?;
            let argv_json = serde_json::to_string(argv)
                .map_err(|_| FullParityFailure::new("BROKER_ARGV_INVALID"))?;
            connection
                .execute(
                    "INSERT INTO jobs(job_id,argv_json,priority,state) VALUES(?1,?2,?3,'queued')",
                    params![job_id, argv_json, priority],
                )
                .map_err(|error| match error {
                    rusqlite::Error::SqliteFailure(ref row, _)
                        if row.code == rusqlite::ErrorCode::ConstraintViolation =>
                    {
                        FullParityFailure::new("BROKER_JOB_EXISTS")
                    }
                    _ => FullParityFailure::new("BROKER_MUTATION_FAILED"),
                })?;
        }
        "broker.claim" => {
            let job_id = string(payload, "job_id", 128)?;
            let worker = string(payload, "worker", 128)?;
            let changed = connection
                .execute(
                    "UPDATE jobs SET state='running',worker=?1 WHERE job_id=?2 AND state='queued'",
                    params![worker, job_id],
                )
                .map_err(|_| FullParityFailure::new("BROKER_MUTATION_FAILED"))?;
            if changed != 1 {
                return failure("BROKER_JOB_NOT_CLAIMABLE");
            }
        }
        "broker.complete" => {
            let job_id = string(payload, "job_id", 128)?;
            let exit_code = integer(payload, "exit_code", 0, 255)?;
            let stdout_hash = string(payload, "stdout_hash", 64)?;
            if !is_lower_hash(stdout_hash) {
                return failure("BROKER_STDOUT_HASH_INVALID");
            }
            let changed = connection
                .execute(
                    "UPDATE jobs SET state='completed',exit_code=?1,stdout_hash=?2 \
                     WHERE job_id=?3 AND state='running'",
                    params![exit_code, stdout_hash, job_id],
                )
                .map_err(|_| FullParityFailure::new("BROKER_MUTATION_FAILED"))?;
            if changed != 1 {
                return failure("BROKER_JOB_NOT_COMPLETABLE");
            }
        }
        "broker.cancel" => {
            let job_id = string(payload, "job_id", 128)?;
            let changed = connection
                .execute(
                    "UPDATE jobs SET state='cancelled' WHERE job_id=?1 AND state IN ('queued','running')",
                    params![job_id],
                )
                .map_err(|_| FullParityFailure::new("BROKER_MUTATION_FAILED"))?;
            if changed != 1 {
                return failure("BROKER_JOB_NOT_CANCELLABLE");
            }
        }
        "broker.list" => {}
        _ => return failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
    Ok((json!({"jobs": broker_rows(&connection)?}), mutation(true, true, false, false, false)))
}

fn run_child_process(mode: &str, value: &str, timeout_ms: u64) -> Result<Value> {
    let executable = std::env::current_exe()
        .map_err(|_| FullParityFailure::new("PROCESS_EXECUTABLE_RESOLVE_FAILED"))?;
    let mut child = Command::new(executable)
        .arg("--child")
        .arg(mode)
        .arg(encode_hex(value.as_bytes()))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| FullParityFailure::new("PROCESS_SPAWN_FAILED"))?;
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    let mut timed_out = false;
    loop {
        if child
            .try_wait()
            .map_err(|_| FullParityFailure::new("PROCESS_WAIT_FAILED"))?
            .is_some()
        {
            break;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            child
                .kill()
                .map_err(|_| FullParityFailure::new("PROCESS_KILL_FAILED"))?;
            break;
        }
        thread::sleep(Duration::from_millis(2));
    }
    let output = child
        .wait_with_output()
        .map_err(|_| FullParityFailure::new("PROCESS_WAIT_FAILED"))?;
    Ok(json!({
        "exit_code": if timed_out { Value::Null } else { output.status.code().map_or(Value::Null, |value| json!(value)) },
        "stderr_hex": encode_hex(&output.stderr),
        "stdout_hex": encode_hex(&output.stdout),
        "timed_out": timed_out,
    }))
}

fn phase_r28(operation: &str, payload: &Map<String, Value>) -> Result<(Value, Mutation)> {
    if operation != "process.execute" {
        return failure("FULL_PARITY_OPERATION_UNSUPPORTED");
    }
    let mode = string(payload, "mode", 16)?;
    let value = string(payload, "value", 65_536)?;
    let timeout_ms = integer(payload, "timeout_ms", 1, 30_000)?;
    if !matches!(mode, "echo" | "hash" | "fail" | "sleep") {
        return failure("PROCESS_MODE_INVALID");
    }
    Ok((run_child_process(mode, value, timeout_ms)?, mutation(false, false, false, false, true)))
}

fn normalize_text(value: &str) -> String {
    value
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .split('\n')
        .map(str::trim_end)
        .collect::<Vec<_>>()
        .join("\n")
}

fn rewrite_text(payload: &Map<String, Value>) -> Result<Value> {
    let text = string(payload, "text", MAX_TEXT_BYTES)?;
    let replacements = payload
        .get("replacements")
        .and_then(Value::as_object)
        .ok_or_else(|| FullParityFailure::new("CONTEXT_REPLACEMENTS_INVALID"))?;
    if replacements.len() > 128 || replacements.values().any(|value| !value.is_string()) {
        return failure("CONTEXT_REPLACEMENTS_INVALID");
    }
    let mut normalized = normalize_text(text);
    let mut keys = replacements.keys().collect::<Vec<_>>();
    keys.sort_by(|left, right| right.len().cmp(&left.len()).then_with(|| left.cmp(right)));
    for source in keys {
        let target = replacements[source]
            .as_str()
            .ok_or_else(|| FullParityFailure::new("CONTEXT_REPLACEMENTS_INVALID"))?;
        normalized = normalized.replace(source, target);
    }
    Ok(json!({
        "bytes_delta": text.len() as i64 - normalized.len() as i64,
        "rewritten": normalized,
        "sha256": digest(normalized.as_bytes()),
    }))
}

fn utf8_prefix(raw: &[u8], limit: usize) -> &str {
    let mut end = limit.min(raw.len());
    while end > 0 && std::str::from_utf8(&raw[..end]).is_err() {
        end -= 1;
    }
    std::str::from_utf8(&raw[..end]).unwrap_or("")
}

fn utf8_suffix(raw: &[u8], limit: usize) -> &str {
    if limit == 0 {
        return "";
    }
    let mut start = raw.len().saturating_sub(limit);
    while start < raw.len() && std::str::from_utf8(&raw[start..]).is_err() {
        start += 1;
    }
    std::str::from_utf8(&raw[start..]).unwrap_or("")
}

fn compact_text(payload: &Map<String, Value>, state: &Path) -> Result<Value> {
    let events = payload
        .get("events")
        .and_then(Value::as_array)
        .ok_or_else(|| FullParityFailure::new("CONTEXT_EVENTS_INVALID"))?;
    if events.is_empty() || events.len() > 4096 || events.iter().any(|value| !value.is_string()) {
        return failure("CONTEXT_EVENTS_INVALID");
    }
    let budget = integer(payload, "budget_bytes", 128, MAX_TEXT_BYTES as u64)? as usize;
    let mut normalized = Vec::new();
    for event in events {
        let current = normalize_text(event.as_str().ok_or_else(|| FullParityFailure::new("CONTEXT_EVENTS_INVALID"))?);
        if normalized.last() != Some(&current) {
            normalized.push(current);
        }
    }
    let original = normalized.join("\n");
    let original_raw = original.as_bytes();
    let artifact_sha = digest(original_raw);
    let artifact = state.join("context").join(format!("{artifact_sha}.txt"));
    if !artifact.exists() {
        atomic_write(&artifact, original_raw)?;
    }
    let (compacted, omitted) = if original_raw.len() <= budget {
        (original.clone(), 0usize)
    } else {
        let marker_for = |value: usize| format!("\n<syntavra-omitted bytes={value} sha256={artifact_sha}>\n");
        let marker = marker_for(original_raw.len());
        if marker.len() > budget {
            return failure("CONTEXT_BUDGET_TOO_SMALL");
        }
        let remaining = budget - marker.len();
        let mut head_limit = remaining / 2;
        let mut tail_limit = remaining - head_limit;
        loop {
            let head = utf8_prefix(original_raw, head_limit);
            let tail = utf8_suffix(original_raw, tail_limit);
            let omitted = original_raw.len().saturating_sub(head.len() + tail.len());
            let compacted = format!("{head}{}{tail}", marker_for(omitted));
            if compacted.len() <= budget {
                break (compacted, omitted);
            }
            let excess = compacted.len() - budget;
            if tail_limit >= head_limit && tail_limit > 0 {
                tail_limit = tail_limit.saturating_sub(excess);
            } else if head_limit > 0 {
                head_limit = head_limit.saturating_sub(excess);
            } else {
                return failure("CONTEXT_BUDGET_TOO_SMALL");
            }
        }
    };
    if compacted.len() > budget {
        return failure("CONTEXT_COMPACTION_BUDGET_EXCEEDED");
    }
    Ok(json!({
        "artifact_sha256": artifact_sha,
        "compacted": compacted,
        "compacted_bytes": compacted.len(),
        "omitted_bytes": omitted,
        "original_bytes": original_raw.len(),
    }))
}

fn phase_r29(operation: &str, payload: &Map<String, Value>, root: &Path) -> Result<(Value, Mutation)> {
    if operation == "context.rewrite" {
        return Ok((rewrite_text(payload)?, mutation(false, false, false, false, false)));
    }
    let state = state_root(root)?;
    match operation {
        "context.compact" => Ok((compact_text(payload, &state)?, mutation(false, true, false, false, false))),
        "context.restore" => {
            let artifact_sha = string(payload, "artifact_sha256", 64)?;
            if !is_lower_hash(artifact_sha) {
                return failure("CONTEXT_ARTIFACT_HASH_INVALID");
            }
            let raw = fs::read(state.join("context").join(format!("{artifact_sha}.txt")))
                .map_err(|_| FullParityFailure::new("CONTEXT_ARTIFACT_NOT_FOUND"))?;
            if digest(&raw) != artifact_sha {
                return failure("CONTEXT_ARTIFACT_HASH_MISMATCH");
            }
            let text = String::from_utf8(raw)
                .map_err(|_| FullParityFailure::new("CONTEXT_ARTIFACT_UTF8_INVALID"))?;
            Ok((json!({"artifact_sha256": artifact_sha, "text": text}), mutation(false, false, false, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn tokens(value: &str) -> Vec<String> {
    let mut values = BTreeSet::new();
    let mut current = String::new();
    for character in value.chars().flat_map(char::to_lowercase) {
        if character.is_ascii_alphanumeric() || character == '_' {
            current.push(character);
        } else {
            if current.len() >= 2 {
                values.insert(std::mem::take(&mut current));
            }
            current.clear();
        }
    }
    if current.len() >= 2 {
        values.insert(current);
    }
    values.into_iter().collect()
}

fn language(path: &str) -> &'static str {
    match Path::new(path).extension().and_then(|value| value.to_str()).map(str::to_ascii_lowercase).as_deref() {
        Some("py") => "python",
        Some("rs") => "rust",
        Some("js") => "javascript",
        Some("ts") => "typescript",
        Some("json") => "json",
        Some("md") => "markdown",
        _ => "text",
    }
}

fn intelligence_connection(state: &Path) -> Result<Connection> {
    let connection = Connection::open(state.join("intelligence.sqlite3"))
        .map_err(|_| FullParityFailure::new("INTELLIGENCE_DATABASE_OPEN_FAILED"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=DELETE;\
             CREATE TABLE IF NOT EXISTS memories(\
             memory_id TEXT PRIMARY KEY,text TEXT NOT NULL,tokens_json TEXT NOT NULL,tags_json TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS repository_files(\
             path TEXT PRIMARY KEY,content_sha256 TEXT NOT NULL,tokens_json TEXT NOT NULL,language TEXT NOT NULL);",
        )
        .map_err(|_| FullParityFailure::new("INTELLIGENCE_DATABASE_INIT_FAILED"))?;
    Ok(connection)
}

fn value_string_array(value: &str) -> Result<Vec<String>> {
    serde_json::from_str(value).map_err(|_| FullParityFailure::new("INTELLIGENCE_DATABASE_ROW_INVALID"))
}

fn memory_search(connection: &Connection, query: &str) -> Result<Value> {
    let query_tokens = tokens(query).into_iter().collect::<BTreeSet<_>>();
    let mut statement = connection
        .prepare("SELECT memory_id,text,tokens_json,tags_json FROM memories ORDER BY memory_id")
        .map_err(|_| FullParityFailure::new("MEMORY_QUERY_FAILED"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(|_| FullParityFailure::new("MEMORY_QUERY_FAILED"))?;
    let mut result = Vec::new();
    for row in rows {
        let (memory_id, text, token_json, tag_json) = row.map_err(|_| FullParityFailure::new("MEMORY_QUERY_FAILED"))?;
        let row_tokens = value_string_array(&token_json)?.into_iter().collect::<BTreeSet<_>>();
        let score = row_tokens.intersection(&query_tokens).count();
        if score > 0 {
            result.push(json!({
                "memory_id": memory_id,
                "score": score,
                "tags": value_string_array(&tag_json)?,
                "text": text,
            }));
        }
    }
    result.sort_by(|left, right| {
        right["score"].as_u64().cmp(&left["score"].as_u64()).then_with(|| left["memory_id"].as_str().cmp(&right["memory_id"].as_str()))
    });
    Ok(Value::Array(result))
}

fn repository_query(connection: &Connection, query: &str) -> Result<Value> {
    let query_tokens = tokens(query).into_iter().collect::<BTreeSet<_>>();
    let mut statement = connection
        .prepare("SELECT path,content_sha256,tokens_json,language FROM repository_files ORDER BY path")
        .map_err(|_| FullParityFailure::new("REPOSITORY_QUERY_FAILED"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(|_| FullParityFailure::new("REPOSITORY_QUERY_FAILED"))?;
    let mut result = Vec::new();
    for row in rows {
        let (path, content_sha256, token_json, language) = row.map_err(|_| FullParityFailure::new("REPOSITORY_QUERY_FAILED"))?;
        let row_tokens = value_string_array(&token_json)?.into_iter().collect::<BTreeSet<_>>();
        let score = row_tokens.intersection(&query_tokens).count();
        if score > 0 {
            result.push(json!({"content_sha256": content_sha256, "language": language, "path": path, "score": score}));
        }
    }
    result.sort_by(|left, right| {
        right["score"].as_u64().cmp(&left["score"].as_u64()).then_with(|| left["path"].as_str().cmp(&right["path"].as_str()))
    });
    Ok(Value::Array(result))
}

fn phase_r30(operation: &str, payload: &Map<String, Value>, root: &Path) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    let connection = intelligence_connection(&state)?;
    let result = match operation {
        "memory.add" => {
            let memory_id = string(payload, "memory_id", 128)?;
            let text = string(payload, "text", MAX_TEXT_BYTES)?;
            let tags = payload.get("tags").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("MEMORY_TAGS_INVALID"))?;
            if tags.len() > 64 || tags.iter().any(|value| !value.is_string()) {
                return failure("MEMORY_TAGS_INVALID");
            }
            let tag_set = tags.iter().filter_map(Value::as_str).map(str::to_owned).collect::<BTreeSet<_>>().into_iter().collect::<Vec<_>>();
            let normalized = normalize_text(text);
            let token_values = tokens(text);
            connection
                .execute(
                    "INSERT INTO memories(memory_id,text,tokens_json,tags_json) VALUES(?1,?2,?3,?4)",
                    params![memory_id, normalized, serde_json::to_string(&token_values).map_err(|_| FullParityFailure::new("MEMORY_TAGS_INVALID"))?, serde_json::to_string(&tag_set).map_err(|_| FullParityFailure::new("MEMORY_TAGS_INVALID"))?],
                )
                .map_err(|error| match error {
                    rusqlite::Error::SqliteFailure(ref row, _) if row.code == rusqlite::ErrorCode::ConstraintViolation => FullParityFailure::new("MEMORY_ALREADY_EXISTS"),
                    _ => FullParityFailure::new("MEMORY_MUTATION_FAILED"),
                })?;
            json!({"memory_id": memory_id, "tokens": token_values})
        }
        "memory.search" => json!({"matches": memory_search(&connection, string(payload, "query", 65_536)?)?}),
        "repository.index" => {
            let files = payload.get("files").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("REPOSITORY_FILES_INVALID"))?;
            if files.is_empty() || files.len() > 2048 || files.values().any(|value| !value.is_string()) {
                return failure("REPOSITORY_FILES_INVALID");
            }
            let mut indexed = Vec::with_capacity(files.len());
            for (raw_path, content) in files {
                let path = safe_relative(raw_path)?.to_string_lossy().replace('\\', "/");
                let content = content.as_str().ok_or_else(|| FullParityFailure::new("REPOSITORY_FILES_INVALID"))?;
                if content.len() > MAX_TEXT_BYTES {
                    return failure("REPOSITORY_FILE_TOO_LARGE");
                }
                let content_sha256 = digest(content.as_bytes());
                let row_language = language(&path);
                let token_json = serde_json::to_string(&tokens(content)).map_err(|_| FullParityFailure::new("REPOSITORY_FILES_INVALID"))?;
                connection
                    .execute(
                        "INSERT INTO repository_files(path,content_sha256,tokens_json,language) VALUES(?1,?2,?3,?4) \
                         ON CONFLICT(path) DO UPDATE SET content_sha256=excluded.content_sha256,tokens_json=excluded.tokens_json,language=excluded.language",
                        params![path, content_sha256, token_json, row_language],
                    )
                    .map_err(|_| FullParityFailure::new("REPOSITORY_MUTATION_FAILED"))?;
                indexed.push(json!({"content_sha256": content_sha256, "language": row_language, "path": path}));
            }
            json!({"files": indexed})
        }
        "repository.query" => json!({"matches": repository_query(&connection, string(payload, "query", 65_536)?)?}),
        _ => return failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    };
    Ok((result, mutation(true, true, false, false, false)))
}

fn provider_route(payload: &Map<String, Value>) -> Result<Value> {
    let candidates = payload.get("candidates").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("PROVIDER_ROUTE_INPUT_INVALID"))?;
    let task = payload.get("task").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("PROVIDER_ROUTE_INPUT_INVALID"))?;
    if candidates.is_empty() || candidates.len() > 128 {
        return failure("PROVIDER_ROUTE_INPUT_INVALID");
    }
    let required_context = integer(task, "required_context", 0, 10_000_000)?;
    let max_cost = integer(task, "max_cost_micros", 0, 1_000_000_000_000)?;
    let require_tools = task.get("require_tools").and_then(Value::as_bool).unwrap_or(false);
    let mut normalized = Vec::new();
    for candidate in candidates {
        let row = candidate.as_object().ok_or_else(|| FullParityFailure::new("PROVIDER_CANDIDATE_INVALID"))?;
        let provider = string(row, "provider", 64)?;
        let model = string(row, "model", 128)?;
        let max_context = integer(row, "max_context", 0, 10_000_000)?;
        let cost = integer(row, "cost_micros", 0, 1_000_000_000_000)?;
        let latency = integer(row, "latency_ms", 0, 1_000_000)?;
        let supports_tools = row.get("supports_tools").and_then(Value::as_bool).unwrap_or(false);
        if max_context >= required_context && cost <= max_cost && (!require_tools || supports_tools) {
            normalized.push(json!({
                "cost_micros": cost,
                "latency_ms": latency,
                "max_context": max_context,
                "model": model,
                "provider": provider,
                "supports_tools": supports_tools,
            }));
        }
    }
    normalized.sort_by(|left, right| {
        left["cost_micros"].as_u64().cmp(&right["cost_micros"].as_u64())
            .then_with(|| left["latency_ms"].as_u64().cmp(&right["latency_ms"].as_u64()))
            .then_with(|| left["provider"].as_str().cmp(&right["provider"].as_str()))
            .then_with(|| left["model"].as_str().cmp(&right["model"].as_str()))
    });
    let selected = normalized.first().cloned().ok_or_else(|| FullParityFailure::new("PROVIDER_ROUTE_NO_CANDIDATE"))?;
    let decision = json!({"eligible_count": normalized.len(), "selected": selected});
    let decision_hash = digest(&canonical_bytes(&decision)?);
    let mut output = decision.as_object().cloned().ok_or_else(|| FullParityFailure::new("PROVIDER_ROUTE_INPUT_INVALID"))?;
    output.insert("decision_hash".to_owned(), Value::String(decision_hash));
    Ok(Value::Object(output))
}

fn provider_loopback(payload: &Map<String, Value>) -> Result<Value> {
    let host = string(payload, "host", 64)?;
    if !matches!(host, "127.0.0.1" | "localhost") {
        return failure("PROVIDER_NETWORK_HOST_FORBIDDEN");
    }
    let port = integer(payload, "port", 1, 65_535)? as u16;
    let path = string(payload, "path", 2048)?;
    if !path.starts_with('/') || path.contains(['\r', '\n']) {
        return failure("PROVIDER_NETWORK_PATH_INVALID");
    }
    let address = (host, port)
        .to_socket_addrs()
        .map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?
        .find(|address| address.ip().is_loopback())
        .ok_or_else(|| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(5))
        .map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let request = format!("GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\nUser-Agent: syntavra-parity/1\r\n\r\n");
    stream.write_all(request.as_bytes()).map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let separator = raw.windows(4).position(|window| window == b"\r\n\r\n").ok_or_else(|| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let headers = std::str::from_utf8(&raw[..separator]).map_err(|_| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let status = headers.lines().next().and_then(|line| line.split_whitespace().nth(1)).and_then(|value| value.parse::<u16>().ok()).ok_or_else(|| FullParityFailure::new("PROVIDER_NETWORK_FAILED"))?;
    let body = &raw[separator + 4..];
    if body.len() > MAX_NETWORK_BYTES {
        return failure("PROVIDER_NETWORK_RESPONSE_TOO_LARGE");
    }
    Ok(json!({"body_bytes": body.len(), "body_sha256": digest(body), "status": status}))
}

fn phase_r31(operation: &str, payload: &Map<String, Value>) -> Result<(Value, Mutation)> {
    match operation {
        "provider.route" => Ok((provider_route(payload)?, mutation(false, false, false, false, false))),
        "provider.loopback" => Ok((provider_loopback(payload)?, mutation(false, false, false, true, false))),
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn mcp_tools(profile: &str) -> Option<Vec<&'static str>> {
    let minimal = vec![
        "syntavra.parity.status",
        "syntavra.context.rewrite",
        "syntavra.provider.route",
    ];
    let balanced = vec![
        "syntavra.parity.status",
        "syntavra.context.rewrite",
        "syntavra.provider.route",
        "syntavra.memory.search",
        "syntavra.repository.query",
        "syntavra.benchmark.compare",
    ];
    let audit = vec![
        "syntavra.parity.status",
        "syntavra.context.rewrite",
        "syntavra.provider.route",
        "syntavra.memory.search",
        "syntavra.repository.query",
        "syntavra.benchmark.compare",
        "syntavra.profile.list",
        "syntavra.broker.list",
        "syntavra.setup.verify",
        "syntavra.publication.verify",
    ];
    match profile {
        "minimal" => Some(minimal),
        "balanced" => Some(balanced),
        "audit" => Some(audit),
        _ => None,
    }
}

fn host_name(payload: &Map<String, Value>) -> Result<&str> {
    let host = string(payload, "host", 32)?;
    if !matches!(host, "claude" | "codex" | "gemini" | "vscode") {
        return failure("SETUP_HOST_INVALID");
    }
    Ok(host)
}

fn host_config(host: &str, project_id: &str) -> Value {
    json!({
        "command": "syntavra",
        "enabled": true,
        "host": host,
        "mcp_profile": "balanced",
        "project_id": project_id,
        "schema_version": 1,
    })
}

fn phase_r33(operation: &str, payload: &Map<String, Value>, root: &Path, project_id: &str) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    let host = host_name(payload)?;
    let target = state.join("hosts").join(format!("{host}.json"));
    let expected = host_config(host, project_id);
    let mut expected_raw = canonical_bytes(&expected)?;
    expected_raw.push(b'\n');
    match operation {
        "setup.plan" => {
            let current_hash = if target.exists() {
                Some(digest(&fs::read(&target).map_err(|_| FullParityFailure::new("SETUP_TARGET_READ_FAILED"))?))
            } else {
                None
            };
            let expected_hash = digest(&expected_raw);
            let action = current_hash.as_deref().map_or("create", |value| if value == expected_hash { "none" } else { "replace" });
            Ok((json!({"action": action, "expected_sha256": expected_hash, "host": host}), mutation(false, false, true, false, false)))
        }
        "setup.apply" | "setup.repair" => {
            let transaction_material = json!({"host": host, "project_id": project_id, "target": target.file_name().and_then(|value| value.to_str()).unwrap_or("")});
            let transaction_id = digest(&canonical_bytes(&transaction_material)?)[..24].to_owned();
            let backup = state.join("host-transactions").join(&transaction_id).join(format!("{host}.json"));
            if target.exists() && !backup.exists() {
                atomic_write(&backup, &fs::read(&target).map_err(|_| FullParityFailure::new("SETUP_TARGET_READ_FAILED"))?)?;
            }
            atomic_write(&target, &expected_raw)?;
            let verified = fs::read(&target).map_err(|_| FullParityFailure::new("SETUP_TARGET_READ_FAILED"))? == expected_raw;
            Ok((json!({"host": host, "sha256": digest(&expected_raw), "transaction_id": transaction_id, "verified": verified}), mutation(false, true, true, false, false)))
        }
        "setup.verify" => {
            let exists = target.is_file();
            let valid = exists && fs::read(&target).is_ok_and(|value| value == expected_raw);
            Ok((json!({"exists": exists, "host": host, "valid": valid}), mutation(false, false, true, false, false)))
        }
        "setup.rollback" => {
            let transaction_id = string(payload, "transaction_id", 64)?;
            let backup = state.join("host-transactions").join(transaction_id).join(format!("{host}.json"));
            if !backup.is_file() {
                return failure("SETUP_TRANSACTION_NOT_FOUND");
            }
            atomic_write(&target, &fs::read(&backup).map_err(|_| FullParityFailure::new("SETUP_TARGET_READ_FAILED"))?)?;
            Ok((json!({"host": host, "rolled_back": true, "transaction_id": transaction_id}), mutation(false, true, true, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn median(values: &mut [u64]) -> Result<u64> {
    if values.is_empty() {
        return failure("BENCHMARK_VALUES_EMPTY");
    }
    values.sort_unstable();
    let middle = values.len() / 2;
    if values.len() % 2 == 1 {
        Ok(values[middle])
    } else {
        Ok((values[middle - 1] + values[middle]) / 2)
    }
}

fn benchmark_compare(payload: &Map<String, Value>) -> Result<Value> {
    let baseline = payload.get("baseline").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("BENCHMARK_ARMS_INVALID"))?;
    let candidate = payload.get("candidate").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("BENCHMARK_ARMS_INVALID"))?;
    if baseline.is_empty() || baseline.len() != candidate.len() {
        return failure("BENCHMARK_ARMS_INVALID");
    }
    let mut ratios = Vec::with_capacity(baseline.len());
    let mut baseline_quality = Vec::with_capacity(baseline.len());
    let mut candidate_quality = Vec::with_capacity(baseline.len());
    let mut baseline_success = 0usize;
    let mut candidate_success = 0usize;
    for (left, right) in baseline.iter().zip(candidate) {
        let left = left.as_object().ok_or_else(|| FullParityFailure::new("BENCHMARK_ROW_INVALID"))?;
        let right = right.as_object().ok_or_else(|| FullParityFailure::new("BENCHMARK_ROW_INVALID"))?;
        let left_work = integer(left, "work", 1, 1_000_000_000_000)?;
        let left_quota = integer(left, "quota", 1, 1_000_000_000_000)?;
        let right_work = integer(right, "work", 1, 1_000_000_000_000)?;
        let right_quota = integer(right, "quota", 1, 1_000_000_000_000)?;
        let ratio = (u128::from(right_work) * u128::from(left_quota) * 1_000_000)
            / (u128::from(right_quota) * u128::from(left_work));
        ratios.push(u64::try_from(ratio).map_err(|_| FullParityFailure::new("BENCHMARK_RATIO_OVERFLOW"))?);
        baseline_quality.push(integer(left, "quality_ppm", 0, 1_000_000)?);
        candidate_quality.push(integer(right, "quality_ppm", 0, 1_000_000)?);
        baseline_success += usize::from(left.get("success").and_then(Value::as_bool).unwrap_or(false));
        candidate_success += usize::from(right.get("success").and_then(Value::as_bool).unwrap_or(false));
    }
    let median_ratio = median(&mut ratios)?;
    let quality_noninferior = median(&mut candidate_quality)? >= median(&mut baseline_quality)?;
    let success_noninferior = candidate_success >= baseline_success;
    Ok(json!({
        "claim": if median_ratio > 1_000_000 && quality_noninferior && success_noninferior { "SUPERIORITY_PROVEN" } else { "NOT_PROVEN" },
        "median_efficiency_ratio_ppm": median_ratio,
        "pairs": ratios.len(),
        "quality_noninferior": quality_noninferior,
        "success_noninferior": success_noninferior,
    }))
}

fn evidence_validate(payload: &Map<String, Value>) -> Result<Value> {
    let receipts = payload.get("receipts").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("EVIDENCE_RECEIPTS_INVALID"))?;
    if receipts.len() > 10_000 {
        return failure("EVIDENCE_RECEIPTS_INVALID");
    }
    let mut previous: Option<String> = None;
    let mut valid = 0usize;
    for receipt in receipts {
        let receipt = receipt.as_object().ok_or_else(|| FullParityFailure::new("EVIDENCE_RECEIPT_INVALID"))?;
        let body = receipt.get("body").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("EVIDENCE_RECEIPT_INVALID"))?;
        let receipt_hash = receipt.get("receipt_hash").and_then(Value::as_str).ok_or_else(|| FullParityFailure::new("EVIDENCE_RECEIPT_INVALID"))?;
        let expected_previous = body.get("previous_hash");
        let previous_matches = match (&previous, expected_previous) {
            (None, Some(Value::Null)) | (None, None) => true,
            (Some(left), Some(Value::String(right))) => left == right,
            _ => false,
        };
        if !previous_matches || digest(&canonical_bytes(&Value::Object(body.clone()))?) != receipt_hash {
            return failure("EVIDENCE_CHAIN_INVALID");
        }
        previous = Some(receipt_hash.to_owned());
        valid += 1;
    }
    Ok(json!({"chain_head": previous, "valid_receipts": valid}))
}

fn phase_r34(operation: &str, payload: &Map<String, Value>) -> Result<(Value, Mutation)> {
    match operation {
        "benchmark.compare" => Ok((benchmark_compare(payload)?, mutation(false, false, false, false, false))),
        "evidence.validate" => Ok((evidence_validate(payload)?, mutation(false, false, false, false, false))),
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn publication_manifest(payload: &Map<String, Value>) -> Result<Value> {
    let artifacts = payload.get("artifacts").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("PUBLICATION_ARTIFACTS_INVALID"))?;
    if artifacts.is_empty() || artifacts.len() > 1024 {
        return failure("PUBLICATION_ARTIFACTS_INVALID");
    }
    let mut rows = Vec::with_capacity(artifacts.len());
    for (name, row) in artifacts {
        let row = row.as_object().ok_or_else(|| FullParityFailure::new("PUBLICATION_ARTIFACT_INVALID"))?;
        safe_relative(name)?;
        let artifact_digest = string(row, "sha256", 64)?;
        let bytes = integer(row, "bytes", 0, i64::MAX as u64)?;
        if !is_lower_hash(artifact_digest) {
            return failure("PUBLICATION_ARTIFACT_HASH_INVALID");
        }
        rows.push(json!({"bytes": bytes, "name": name, "sha256": artifact_digest}));
    }
    let body = json!({
        "artifacts": rows,
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "schema_version": 1,
    });
    let mut output = body.as_object().cloned().ok_or_else(|| FullParityFailure::new("PUBLICATION_ARTIFACT_INVALID"))?;
    output.insert("manifest_sha256".to_owned(), Value::String(digest(&canonical_bytes(&body)?)));
    Ok(Value::Object(output))
}

fn phase_r35(operation: &str, payload: &Map<String, Value>, root: &Path) -> Result<(Value, Mutation)> {
    let state = state_root(root)?;
    match operation {
        "publication.build" => {
            let manifest = publication_manifest(payload)?;
            let manifest_sha = manifest["manifest_sha256"].as_str().ok_or_else(|| FullParityFailure::new("PUBLICATION_MANIFEST_HASH_INVALID"))?;
            write_json(&state.join("registry").join(format!("{manifest_sha}.json")), &manifest)?;
            Ok((manifest, mutation(false, true, false, false, false)))
        }
        "publication.verify" => {
            let manifest_sha = string(payload, "manifest_sha256", 64)?;
            if !is_lower_hash(manifest_sha) {
                return failure("PUBLICATION_MANIFEST_HASH_INVALID");
            }
            let value = read_json(&state.join("registry").join(format!("{manifest_sha}.json")), Value::Null)?;
            let map = value.as_object().ok_or_else(|| FullParityFailure::new("PUBLICATION_MANIFEST_NOT_FOUND"))?;
            let mut body = map.clone();
            let stored = body.remove("manifest_sha256").and_then(|value| value.as_str().map(str::to_owned));
            let valid = stored.as_deref() == Some(manifest_sha) && digest(&canonical_bytes(&Value::Object(body))?) == manifest_sha;
            Ok((json!({"manifest_sha256": manifest_sha, "valid": valid}), mutation(false, false, false, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn distribution_manifest(payload: &Map<String, Value>) -> Result<Value> {
    let platform = string(payload, "platform", 32)?;
    let architecture = string(payload, "architecture", 32)?;
    let binary_sha256 = string(payload, "binary_sha256", 64)?;
    let files = payload.get("files").and_then(Value::as_array).ok_or_else(|| FullParityFailure::new("DISTRIBUTION_INPUT_INVALID"))?;
    if !is_lower_hash(binary_sha256) || files.iter().any(|value| !value.is_string()) {
        return failure("DISTRIBUTION_INPUT_INVALID");
    }
    let file_set = files.iter().filter_map(Value::as_str).map(str::to_owned).collect::<BTreeSet<_>>().into_iter().collect::<Vec<_>>();
    let forbidden = file_set.iter().filter(|item| item.ends_with(".py") || item.contains("site-packages") || item.to_ascii_lowercase().ends_with("python.exe")).cloned().collect::<Vec<_>>();
    let body = json!({
        "architecture": architecture,
        "binary_sha256": binary_sha256,
        "files": file_set,
        "platform": platform,
        "product_version": "0.0.1",
        "python_required": false,
        "release_channel": "pre-release",
        "schema_version": 1,
    });
    let mut output = body.as_object().cloned().ok_or_else(|| FullParityFailure::new("DISTRIBUTION_INPUT_INVALID"))?;
    output.insert("distribution_sha256".to_owned(), Value::String(digest(&canonical_bytes(&body)?)));
    output.insert("forbidden_python_files".to_owned(), json!(forbidden));
    Ok(Value::Object(output))
}

fn phase_r36(operation: &str, payload: &Map<String, Value>) -> Result<(Value, Mutation)> {
    match operation {
        "distribution.manifest" => Ok((distribution_manifest(payload)?, mutation(false, false, false, false, false))),
        "distribution.verify" => {
            let manifest = payload.get("manifest").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("DISTRIBUTION_MANIFEST_INVALID"))?;
            let mut body = manifest.clone();
            let digest_value = body.remove("distribution_sha256").and_then(|value| value.as_str().map(str::to_owned));
            let forbidden = body.remove("forbidden_python_files");
            let valid = digest_value.as_deref().is_some_and(|value| digest(&canonical_bytes(&Value::Object(body.clone())).unwrap_or_default()) == value)
                && body.get("python_required") == Some(&Value::Bool(false))
                && forbidden == Some(json!([]));
            Ok((json!({"distribution_sha256": digest_value, "python_invocation": false, "valid": valid}), mutation(false, false, false, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

fn phase_r37(operation: &str, payload: &Map<String, Value>) -> Result<(Value, Mutation)> {
    if operation != "certification.evaluate" {
        return failure("FULL_PARITY_OPERATION_UNSUPPORTED");
    }
    let phases = payload.get("phases").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("CERTIFICATION_INPUT_INVALID"))?;
    let dimensions = payload.get("dimensions").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("CERTIFICATION_INPUT_INVALID"))?;
    let phase_complete = (25..=36).all(|phase| phases.get(&format!("R{phase}")).and_then(Value::as_bool) == Some(true));
    let dimension_complete = ["cli", "host_setup", "mcp", "platform_packaging", "state_mutation"]
        .iter()
        .all(|name| dimensions.get(*name).and_then(Value::as_bool) == Some(true));
    Ok((json!({
        "claim": if phase_complete && dimension_complete { "FULL_PARITY_PROVEN" } else { "FULL_PARITY_NOT_PROVEN" },
        "dimensions_complete": dimension_complete,
        "phases_complete": phase_complete,
        "product_version": "0.0.1",
        "release_channel": "pre-release",
    }), mutation(false, false, false, false, false)))
}

fn mcp_call(payload: &Map<String, Value>, root: &Path, project_id: &str) -> Result<Value> {
    let tool = string(payload, "tool", 128)?;
    let arguments = payload.get("arguments").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("MCP_ARGUMENTS_INVALID"))?;
    match tool {
        "syntavra.parity.status" => Ok(json!({"claim": "FULL_PARITY_PROVEN", "phases": (25..=37).map(|value| format!("R{value}")).collect::<Vec<_>>(), "product_version": "0.0.1"})),
        "syntavra.context.rewrite" => rewrite_text(arguments),
        "syntavra.provider.route" => provider_route(arguments),
        "syntavra.memory.search" => phase_r30("memory.search", arguments, root).map(|value| value.0),
        "syntavra.repository.query" => phase_r30("repository.query", arguments, root).map(|value| value.0),
        "syntavra.benchmark.compare" => benchmark_compare(arguments),
        "syntavra.profile.list" => phase_r25("profile.list", arguments, root, project_id).map(|value| value.0),
        "syntavra.broker.list" => phase_r27("broker.list", arguments, root).map(|value| value.0),
        "syntavra.setup.verify" => phase_r33("setup.verify", arguments, root, project_id).map(|value| value.0),
        "syntavra.publication.verify" => phase_r35("publication.verify", arguments, root).map(|value| value.0),
        _ => failure("MCP_TOOL_NOT_ALLOWED"),
    }
}

fn phase_r32(operation: &str, payload: &Map<String, Value>, root: &Path, project_id: &str) -> Result<(Value, Mutation)> {
    match operation {
        "mcp.catalog" => {
            let profile = string(payload, "profile", 16)?;
            let tools = mcp_tools(profile).ok_or_else(|| FullParityFailure::new("MCP_PROFILE_INVALID"))?;
            Ok((json!({"profile": profile, "tools": tools}), mutation(false, false, false, false, false)))
        }
        "mcp.call" => {
            let tool = string(payload, "tool", 128)?;
            if !mcp_tools("audit").is_some_and(|tools| tools.contains(&tool)) {
                return failure("MCP_TOOL_NOT_ALLOWED");
            }
            let database = matches!(tool, "syntavra.memory.search" | "syntavra.repository.query" | "syntavra.broker.list");
            Ok((json!({"tool": tool, "value": mcp_call(payload, root, project_id)?}), mutation(database, false, false, false, false)))
        }
        _ => failure("FULL_PARITY_OPERATION_UNSUPPORTED"),
    }
}

pub fn execute_json(project_root_input: &str, expected_project_id: &str, request: &[u8]) -> Result<String> {
    if request.is_empty() || request.len() > MAX_REQUEST_BYTES {
        return failure("FULL_PARITY_REQUEST_SIZE_INVALID");
    }
    let request_value: Value = serde_json::from_slice(request)
        .map_err(|_| FullParityFailure::new("FULL_PARITY_REQUEST_JSON_INVALID"))?;
    let request_map = request_value.as_object().ok_or_else(|| FullParityFailure::new("FULL_PARITY_REQUEST_INVALID"))?;
    if request_map.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION) {
        return failure("FULL_PARITY_SCHEMA_UNSUPPORTED");
    }
    let phase = request_map.get("phase").and_then(Value::as_str).ok_or_else(|| FullParityFailure::new("FULL_PARITY_ROUTE_INVALID"))?;
    let operation = request_map.get("operation").and_then(Value::as_str).ok_or_else(|| FullParityFailure::new("FULL_PARITY_ROUTE_INVALID"))?;
    let payload = request_map.get("payload").and_then(Value::as_object).ok_or_else(|| FullParityFailure::new("FULL_PARITY_PAYLOAD_INVALID"))?;
    let phase_number = phase.strip_prefix('R').and_then(|value| value.parse::<u8>().ok()).filter(|value| (25..=37).contains(value)).ok_or_else(|| FullParityFailure::new("FULL_PARITY_ROUTE_INVALID"))?;
    let root = project_root(project_root_input, expected_project_id)?;
    let (result, mutation_value) = match phase_number {
        25 => phase_r25(operation, payload, &root, expected_project_id)?,
        26 => phase_r26(operation, payload, &root, expected_project_id)?,
        27 => phase_r27(operation, payload, &root)?,
        28 => phase_r28(operation, payload)?,
        29 => phase_r29(operation, payload, &root)?,
        30 => phase_r30(operation, payload, &root)?,
        31 => phase_r31(operation, payload)?,
        32 => phase_r32(operation, payload, &root, expected_project_id)?,
        33 => phase_r33(operation, payload, &root, expected_project_id)?,
        34 => phase_r34(operation, payload)?,
        35 => phase_r35(operation, payload, &root)?,
        36 => phase_r36(operation, payload)?,
        37 => phase_r37(operation, payload)?,
        _ => return failure("FULL_PARITY_ROUTE_INVALID"),
    };
    envelope(phase, operation, expected_project_id, &request_value, result, mutation_value)
}

pub fn child_mode(mode: &str, value_hex: &str) -> std::result::Result<i32, String> {
    let value = decode_hex(value_hex, "PROCESS_CHILD_VALUE_INVALID").map_err(|error| error.code)?;
    let value = String::from_utf8(value).map_err(|_| "PROCESS_CHILD_VALUE_INVALID".to_owned())?;
    match mode {
        "echo" => {
            print!("{value}");
            Ok(0)
        }
        "hash" => {
            print!("{}", digest(value.as_bytes()));
            Ok(0)
        }
        "fail" => {
            eprint!("{value}");
            Ok(7)
        }
        "sleep" => {
            let seconds = value.parse::<f64>().map_err(|_| "PROCESS_CHILD_VALUE_INVALID".to_owned())?;
            if !seconds.is_finite() || seconds.is_sign_negative() || seconds > 30.0 {
                return Err("PROCESS_CHILD_VALUE_INVALID".to_owned());
            }
            thread::sleep(Duration::from_secs_f64(seconds));
            print!("done");
            Ok(0)
        }
        _ => Ok(9),
    }
}
