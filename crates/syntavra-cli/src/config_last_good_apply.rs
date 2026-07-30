#![forbid(unsafe_code)]

use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use crate::config_contract::{resolve_config_wire, snapshot_json};
use crate::config_last_good_plan::plan_json;

const SCHEMA_VERSION: u32 = 1;
const CONTRACT_VERSION: u32 = 1;
const APPLY_ID: &str = "syntavra-config-last-good-apply-v1";
const CLAIM: &str = "RUST_CONFIG_LAST_GOOD_APPLY_PARITY_PROVEN_R25_FIXTURES";
const TARGET_RELATIVE_PATH: &str = ".syntavra/pre-release/config-last-good.json";
const LOCK_RELATIVE_PATH: &str = ".syntavra/pre-release/config-last-good.lock";
const TEMP_RELATIVE_PATH: &str = ".syntavra/pre-release/.config-last-good.json.apply.tmp";
const STALE_LOCK_SECONDS: u64 = 300;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyFailure {
    pub code: String,
    pub crash_simulated: bool,
}

impl ApplyFailure {
    fn normal(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            crash_simulated: false,
        }
    }

    fn crash(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            crash_simulated: true,
        }
    }

    fn from_code(code: String) -> Self {
        Self::normal(code)
    }
}

fn map_io<T>(result: std::io::Result<T>, code: &str) -> Result<T, ApplyFailure> {
    result.map_err(|_| ApplyFailure::normal(code))
}

fn reject_symlink(path: &Path, code: &str) -> Result<(), ApplyFailure> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(ApplyFailure::normal(code)),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(_) => Err(ApplyFailure::normal(code)),
    }
}

fn prepare_state_directory(project_root: &Path) -> Result<PathBuf, ApplyFailure> {
    let state = project_root.join(".syntavra");
    let release = state.join("pre-release");
    reject_symlink(&state, "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK")?;
    reject_symlink(&release, "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK")?;
    map_io(
        fs::create_dir_all(&release),
        "CONFIG_LIFECYCLE_STATE_ROOT_CREATE_FAILED",
    )?;
    reject_symlink(&state, "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK")?;
    reject_symlink(&release, "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK")?;
    Ok(release)
}

fn sync_directory(path: &Path) {
    if let Ok(file) = File::open(path) {
        let _ = file.sync_all();
    }
}

#[cfg(unix)]
fn set_private_mode(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(metadata) = fs::metadata(path) {
        let mut permissions = metadata.permissions();
        permissions.set_mode(0o600);
        let _ = fs::set_permissions(path, permissions);
    }
}

#[cfg(not(unix))]
fn set_private_mode(_path: &Path) {}

fn canonical_candidate_payload(config_wire: &[u8]) -> Result<String, ApplyFailure> {
    let snapshot = resolve_config_wire(config_wire)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_CONFIG_INVALID"))?;
    let rendered = snapshot_json(&snapshot)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_SNAPSHOT_JSON_INVALID"))?;
    let value: Value = serde_json::from_str(&rendered)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_SNAPSHOT_JSON_INVALID"))?;
    let Some(object) = value.as_object() else {
        return Err(ApplyFailure::normal(
            "CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID",
        ));
    };
    let expected = [
        "schema_version",
        "values",
        "provenance",
        "config_hash",
        "warnings",
    ];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(ApplyFailure::normal(
            "CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID",
        ));
    }
    serde_json::to_string(&value)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_SNAPSHOT_JSON_INVALID"))
}

fn normalized_json_payload(raw: &[u8]) -> Result<String, ApplyFailure> {
    let payload = raw.strip_suffix(b"\n").unwrap_or(raw);
    if payload.is_empty() || payload.ends_with(b"\n") {
        return Err(ApplyFailure::normal(
            "CONFIG_LIFECYCLE_TARGET_ENCODING_INVALID",
        ));
    }
    let value: Value = serde_json::from_slice(payload)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_TARGET_JSON_INVALID"))?;
    if !value.is_object() {
        return Err(ApplyFailure::normal("CONFIG_LIFECYCLE_TARGET_JSON_INVALID"));
    }
    serde_json::to_string(&value)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_TARGET_JSON_INVALID"))
}

fn validate_target_snapshot(
    raw: &[u8],
    expected_config_hash: Option<&str>,
) -> Result<String, ApplyFailure> {
    let canonical = normalized_json_payload(raw)?;
    let value: Value = serde_json::from_str(&canonical)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_TARGET_JSON_INVALID"))?;
    let Some(object) = value.as_object() else {
        return Err(ApplyFailure::normal("CONFIG_LIFECYCLE_TARGET_JSON_INVALID"));
    };
    for key in ["schema_version", "values", "provenance", "config_hash"] {
        if !object.contains_key(key) {
            return Err(ApplyFailure::normal(
                "CONFIG_LIFECYCLE_TARGET_SCHEMA_INVALID",
            ));
        }
    }
    if object.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return Err(ApplyFailure::normal(
            "CONFIG_LIFECYCLE_TARGET_SCHEMA_INVALID",
        ));
    }
    if let Some(expected) = expected_config_hash {
        if object.get("config_hash").and_then(Value::as_str) != Some(expected) {
            return Err(ApplyFailure::normal(
                "CONFIG_LIFECYCLE_TARGET_HASH_MISMATCH",
            ));
        }
    }
    Ok(canonical)
}

fn read_bytes(path: &Path, code: &str) -> Result<Vec<u8>, ApplyFailure> {
    let mut file = map_io(File::open(path), code)?;
    let mut bytes = Vec::new();
    map_io(file.read_to_end(&mut bytes), code)?;
    Ok(bytes)
}

fn lock_payload(project_id: &str) -> Result<Vec<u8>, ApplyFailure> {
    let value = json!({
        "contract_version": CONTRACT_VERSION,
        "project_id": project_id,
        "target": TARGET_RELATIVE_PATH,
    });
    let mut bytes = serde_json::to_vec(&value)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_LOCK_WRITE_FAILED"))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn current_unix_seconds() -> Result<u64, ApplyFailure> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_CLOCK_INVALID"))
}

fn lock_is_stale(path: &Path, now_unix: u64) -> Result<bool, ApplyFailure> {
    let modified = map_io(
        fs::metadata(path).and_then(|metadata| metadata.modified()),
        "CONFIG_LIFECYCLE_LOCK_METADATA_FAILED",
    )?;
    let modified_unix = modified
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(now_unix);
    Ok(now_unix.saturating_sub(modified_unix) >= STALE_LOCK_SECONDS)
}

fn validate_lock_binding(path: &Path, project_id: &str) -> Result<(), ApplyFailure> {
    let raw = read_bytes(path, "CONFIG_LIFECYCLE_STALE_LOCK_INVALID")?;
    let value: Value = serde_json::from_slice(&raw)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_STALE_LOCK_INVALID"))?;
    if value.get("contract_version").and_then(Value::as_u64) != Some(u64::from(CONTRACT_VERSION))
        || value.get("project_id").and_then(Value::as_str) != Some(project_id)
        || value.get("target").and_then(Value::as_str) != Some(TARGET_RELATIVE_PATH)
    {
        return Err(ApplyFailure::normal("CONFIG_LIFECYCLE_STALE_LOCK_INVALID"));
    }
    Ok(())
}

fn write_lock(file: &mut File, project_id: &str) -> Result<(), ApplyFailure> {
    let payload = lock_payload(project_id)?;
    map_io(
        file.write_all(&payload),
        "CONFIG_LIFECYCLE_LOCK_WRITE_FAILED",
    )?;
    map_io(file.flush(), "CONFIG_LIFECYCLE_LOCK_WRITE_FAILED")?;
    map_io(file.sync_all(), "CONFIG_LIFECYCLE_LOCK_WRITE_FAILED")
}

fn acquire_lock(path: &Path, project_id: &str, now_unix: u64) -> Result<bool, ApplyFailure> {
    reject_symlink(path, "CONFIG_LIFECYCLE_LOCK_SYMLINK")?;
    let open = || OpenOptions::new().write(true).create_new(true).open(path);
    match open() {
        Ok(mut file) => {
            write_lock(&mut file, project_id)?;
            set_private_mode(path);
            Ok(false)
        }
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            reject_symlink(path, "CONFIG_LIFECYCLE_LOCK_SYMLINK")?;
            if !lock_is_stale(path, now_unix)? {
                return Err(ApplyFailure::normal("CONFIG_LIFECYCLE_LOCK_HELD"));
            }
            validate_lock_binding(path, project_id)?;
            map_io(
                fs::remove_file(path),
                "CONFIG_LIFECYCLE_STALE_LOCK_REMOVE_FAILED",
            )?;
            let mut file = map_io(open(), "CONFIG_LIFECYCLE_LOCK_ACQUIRE_FAILED")?;
            write_lock(&mut file, project_id)?;
            set_private_mode(path);
            Ok(true)
        }
        Err(_) => Err(ApplyFailure::normal("CONFIG_LIFECYCLE_LOCK_ACQUIRE_FAILED")),
    }
}

fn fault(point: Option<&str>, expected: &str) -> Result<(), ApplyFailure> {
    if point == Some(expected) {
        return Err(ApplyFailure::crash(format!(
            "CONFIG_LIFECYCLE_FAULT_INJECTED_{}",
            expected.replace('-', "_").to_ascii_uppercase()
        )));
    }
    Ok(())
}

fn write_temp(path: &Path, payload: &[u8]) -> Result<(), ApplyFailure> {
    reject_symlink(path, "CONFIG_LIFECYCLE_TEMP_SYMLINK")?;
    let mut file = map_io(
        OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(path),
        "CONFIG_LIFECYCLE_TEMP_WRITE_FAILED",
    )?;
    map_io(
        file.write_all(payload),
        "CONFIG_LIFECYCLE_TEMP_WRITE_FAILED",
    )?;
    map_io(file.write_all(b"\n"), "CONFIG_LIFECYCLE_TEMP_WRITE_FAILED")?;
    map_io(file.flush(), "CONFIG_LIFECYCLE_TEMP_WRITE_FAILED")?;
    map_io(file.sync_all(), "CONFIG_LIFECYCLE_TEMP_WRITE_FAILED")?;
    set_private_mode(path);
    Ok(())
}

fn receipt_json(
    action: &str,
    decision: &str,
    project_id: &str,
    candidate: &Value,
    stored_payload: &str,
    stale_lock_recovered: bool,
    filesystem_mutated: bool,
) -> Result<String, ApplyFailure> {
    let candidate_hash = candidate
        .get("config_hash")
        .and_then(Value::as_str)
        .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;
    let payload_bytes = candidate
        .get("payload_bytes")
        .and_then(Value::as_u64)
        .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;
    let payload_sha256 = candidate
        .get("payload_sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;

    let receipt = json!({
        "apply_authority": "bounded-shadow",
        "apply_id": APPLY_ID,
        "claim": CLAIM,
        "contract_version": CONTRACT_VERSION,
        "decision": decision,
        "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        "lock": {
            "relative_path": LOCK_RELATIVE_PATH,
            "stale_recovered": stale_lock_recovered,
        },
        "mutation": {
            "database_opened": false,
            "filesystem": filesystem_mutated,
        },
        "ok": true,
        "project_id": project_id,
        "public_routing": "blocked",
        "result": {
            "action": action,
            "config_hash": candidate_hash,
            "payload_bytes": payload_bytes,
            "payload_sha256": payload_sha256,
            "stored_payload_bytes": stored_payload.len(),
            "stored_payload_sha256": sha256_hex(stored_payload.as_bytes()),
        },
        "schema_version": SCHEMA_VERSION,
        "target": {
            "file_mode": "0600",
            "relative_path": TARGET_RELATIVE_PATH,
        },
    });
    serde_json::to_string(&receipt)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_RECEIPT_JSON_INVALID"))
}

struct Transaction<'a> {
    release: &'a Path,
    target: &'a Path,
    temp: &'a Path,
    project_id: &'a str,
    decision: &'a str,
    candidate: &'a Value,
    candidate_payload: &'a str,
    stale_recovered: bool,
    fault: Option<&'a str>,
}

impl Transaction<'_> {
    fn candidate_string(&self, key: &str) -> Result<&str, ApplyFailure> {
        self.candidate
            .get(key)
            .and_then(Value::as_str)
            .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))
    }

    fn recover_temp(&self) -> Result<Option<String>, ApplyFailure> {
        if !self.temp.is_file() {
            return Ok(None);
        }

        let raw = read_bytes(self.temp, "CONFIG_LIFECYCLE_TEMP_READ_FAILED")?;
        let normalized = normalized_json_payload(&raw)?;
        let candidate_sha = self.candidate_string("payload_sha256")?;
        if sha256_hex(normalized.as_bytes()) == candidate_sha
            && !self.target.exists()
            && self.decision == "write"
        {
            map_io(
                fs::rename(self.temp, self.target),
                "CONFIG_LIFECYCLE_REPLACE_FAILED",
            )?;
            set_private_mode(self.target);
            sync_directory(self.release);
            let stored = validate_target_snapshot(
                &read_bytes(self.target, "CONFIG_LIFECYCLE_TARGET_READ_FAILED")?,
                Some(self.candidate_string("config_hash")?),
            )?;
            return receipt_json(
                "recover-temp",
                self.decision,
                self.project_id,
                self.candidate,
                &stored,
                self.stale_recovered,
                true,
            )
            .map(Some);
        }

        map_io(
            fs::remove_file(self.temp),
            "CONFIG_LIFECYCLE_TEMP_REMOVE_FAILED",
        )?;
        Ok(None)
    }

    fn inspect_target(&self) -> Result<Option<String>, ApplyFailure> {
        if !self.target.is_file() {
            if self.decision == "retain-existing" {
                return Err(ApplyFailure::normal(
                    "CONFIG_LIFECYCLE_RETAIN_TARGET_MISSING",
                ));
            }
            return Ok(None);
        }

        let raw = read_bytes(self.target, "CONFIG_LIFECYCLE_TARGET_READ_FAILED")?;
        let expected_hash = if self.decision == "retain-existing" {
            Some(self.candidate_string("config_hash")?)
        } else {
            None
        };
        let current = validate_target_snapshot(&raw, expected_hash)?;
        if self.decision == "retain-existing" {
            return receipt_json(
                "retain-existing",
                self.decision,
                self.project_id,
                self.candidate,
                &current,
                self.stale_recovered,
                false,
            )
            .map(Some);
        }
        if sha256_hex(current.as_bytes()) == self.candidate_string("payload_sha256")? {
            return receipt_json(
                "already-current",
                self.decision,
                self.project_id,
                self.candidate,
                &current,
                self.stale_recovered,
                false,
            )
            .map(Some);
        }
        Ok(None)
    }

    fn write_candidate(&self) -> Result<String, ApplyFailure> {
        write_temp(self.temp, self.candidate_payload.as_bytes())?;
        fault(self.fault, "after-temp-sync")?;
        map_io(
            fs::rename(self.temp, self.target),
            "CONFIG_LIFECYCLE_REPLACE_FAILED",
        )?;
        set_private_mode(self.target);
        fault(self.fault, "after-replace")?;
        sync_directory(self.release);

        let stored = validate_target_snapshot(
            &read_bytes(self.target, "CONFIG_LIFECYCLE_TARGET_READ_FAILED")?,
            Some(self.candidate_string("config_hash")?),
        )?;
        if sha256_hex(stored.as_bytes()) != self.candidate_string("payload_sha256")? {
            return Err(ApplyFailure::normal(
                "CONFIG_LIFECYCLE_POST_WRITE_VERIFY_FAILED",
            ));
        }
        receipt_json(
            "write",
            self.decision,
            self.project_id,
            self.candidate,
            &stored,
            self.stale_recovered,
            true,
        )
    }

    fn execute(&self) -> Result<String, ApplyFailure> {
        fault(self.fault, "after-lock")?;
        reject_symlink(self.target, "CONFIG_LIFECYCLE_TARGET_SYMLINK")?;
        reject_symlink(self.temp, "CONFIG_LIFECYCLE_TEMP_SYMLINK")?;
        if let Some(receipt) = self.recover_temp()? {
            return Ok(receipt);
        }
        if let Some(receipt) = self.inspect_target()? {
            return Ok(receipt);
        }
        self.write_candidate()
    }
}

pub fn apply_json(
    project_root: &str,
    expected_project_id: &str,
    config_wire: &[u8],
    fault_point: Option<&str>,
    now_unix: Option<u64>,
) -> Result<String, ApplyFailure> {
    if let Some(point) = fault_point {
        if !matches!(point, "after-lock" | "after-temp-sync" | "after-replace") {
            return Err(ApplyFailure::normal("CONFIG_LIFECYCLE_FAULT_POINT_INVALID"));
        }
    }

    let plan_rendered = plan_json(project_root, expected_project_id, config_wire)
        .map_err(ApplyFailure::from_code)?;
    let plan: Value = serde_json::from_str(&plan_rendered)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;
    let candidate = plan
        .get("candidate")
        .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;
    let decision = plan
        .get("decision")
        .and_then(Value::as_str)
        .ok_or_else(|| ApplyFailure::normal("CONFIG_LIFECYCLE_PLAN_INVALID"))?;
    let candidate_payload = canonical_candidate_payload(config_wire)?;
    let candidate_digest = sha256_hex(candidate_payload.as_bytes());
    if candidate.get("payload_bytes").and_then(Value::as_u64)
        != Some(candidate_payload.len() as u64)
        || candidate.get("payload_sha256").and_then(Value::as_str)
            != Some(candidate_digest.as_str())
    {
        return Err(ApplyFailure::normal(
            "CONFIG_LIFECYCLE_PLAN_PAYLOAD_MISMATCH",
        ));
    }

    let root = fs::canonicalize(project_root)
        .map_err(|_| ApplyFailure::normal("CONFIG_LIFECYCLE_PROJECT_ROOT_CHANGED"))?;
    let target = root.join(TARGET_RELATIVE_PATH);
    let lock = root.join(LOCK_RELATIVE_PATH);
    let temp = root.join(TEMP_RELATIVE_PATH);
    if decision == "retain-existing" {
        reject_symlink(
            &root.join(".syntavra"),
            "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK",
        )?;
        reject_symlink(
            &root.join(".syntavra").join("pre-release"),
            "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK",
        )?;
        reject_symlink(&target, "CONFIG_LIFECYCLE_TARGET_SYMLINK")?;
        if !target.is_file() {
            return Err(ApplyFailure::normal(
                "CONFIG_LIFECYCLE_RETAIN_TARGET_MISSING",
            ));
        }
    }
    let release = prepare_state_directory(&root)?;
    let effective_now = match now_unix {
        Some(value) => value,
        None => current_unix_seconds()?,
    };
    let stale_recovered = acquire_lock(&lock, expected_project_id, effective_now)?;

    let transaction = Transaction {
        release: &release,
        target: &target,
        temp: &temp,
        project_id: expected_project_id,
        decision,
        candidate,
        candidate_payload: &candidate_payload,
        stale_recovered,
        fault: fault_point,
    };
    let result = transaction.execute();
    let crash_simulated = result
        .as_ref()
        .err()
        .is_some_and(|error| error.crash_simulated);
    if !crash_simulated {
        let _ = fs::remove_file(&temp);
        let _ = fs::remove_file(&lock);
        sync_directory(&release);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::{fault, normalized_json_payload, ApplyFailure};

    #[test]
    fn canonicalizes_one_trailing_newline() {
        assert_eq!(
            normalized_json_payload(b"{\"b\":2,\"a\":1}\n"),
            Ok("{\"a\":1,\"b\":2}".to_owned())
        );
    }

    #[test]
    fn crash_faults_are_marked() {
        assert_eq!(
            fault(Some("after-lock"), "after-lock"),
            Err(ApplyFailure::crash(
                "CONFIG_LIFECYCLE_FAULT_INJECTED_AFTER_LOCK"
            ))
        );
    }
}
