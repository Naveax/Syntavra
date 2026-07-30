#![forbid(unsafe_code)]

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use crate::config_contract::{resolve_config_wire, snapshot_json};
use crate::config_last_good_plan::plan_json;

const SCHEMA_VERSION: u32 = 1;
const CONTRACT_ID: &str = "syntavra-config-last-good-atomic-apply-v1";
const CLAIM: &str = "RUST_CONFIG_LAST_GOOD_ATOMIC_APPLY_PARITY_PROVEN_R25_FIXTURES";
const TARGET_RELATIVE_PATH: &str = ".syntavra/pre-release/config-last-good.json";
const LOCK_RELATIVE_PATH: &str = ".syntavra/pre-release/config-last-good.lock";
const MAX_PAYLOAD_BYTES: usize = 256 * 1024;
static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

fn map_plan_error(error: &str) -> String {
    error.replace("CONFIG_LIFECYCLE_", "CONFIG_LAST_GOOD_APPLY_")
}

fn canonical_payload(config_wire: &[u8]) -> Result<Vec<u8>, String> {
    let snapshot = resolve_config_wire(config_wire)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned())?;
    let rendered = snapshot_json(&snapshot)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned())?;
    let value: Value = serde_json::from_str(&rendered)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned())?;
    let Some(object) = value.as_object() else {
        return Err("CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned());
    };
    let expected = [
        "config_hash",
        "provenance",
        "schema_version",
        "values",
        "warnings",
    ];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err("CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned());
    }
    let mut payload = serde_json::to_vec(&value)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned())?;
    payload.push(b'\n');
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err("CONFIG_LAST_GOOD_APPLY_PAYLOAD_TOO_LARGE".to_owned());
    }
    Ok(payload)
}

fn inspect_optional(path: &Path, symlink_error: &str, type_error: &str) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() {
                return Err(symlink_error.to_owned());
            }
            if !metadata.is_file() {
                return Err(type_error.to_owned());
            }
            Ok(true)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err("CONFIG_LAST_GOOD_APPLY_PATH_INSPECTION_FAILED".to_owned()),
    }
}

fn ensure_secure_parent(root: &Path) -> Result<(PathBuf, Vec<PathBuf>), String> {
    let syntavra = root.join(".syntavra");
    let parent = syntavra.join("pre-release");
    let mut created = Vec::new();
    for path in [&syntavra, &parent] {
        match fs::symlink_metadata(path) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink() {
                    return Err("CONFIG_LAST_GOOD_APPLY_PARENT_SYMLINK".to_owned());
                }
                if !metadata.is_dir() {
                    return Err("CONFIG_LAST_GOOD_APPLY_PARENT_TYPE_INVALID".to_owned());
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                match fs::create_dir(path) {
                    Ok(()) => created.push(path.to_path_buf()),
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                    Err(_) => return Err("CONFIG_LAST_GOOD_APPLY_PARENT_CREATE_FAILED".to_owned()),
                }
                let metadata = fs::symlink_metadata(path)
                    .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PARENT_RACE".to_owned())?;
                if metadata.file_type().is_symlink() || !metadata.is_dir() {
                    return Err("CONFIG_LAST_GOOD_APPLY_PARENT_RACE".to_owned());
                }
            }
            Err(_) => return Err("CONFIG_LAST_GOOD_APPLY_PATH_INSPECTION_FAILED".to_owned()),
        }
    }
    Ok((parent, created))
}

fn read_bounded(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = fs::metadata(path)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TARGET_READ_FAILED".to_owned())?;
    if metadata.len() > MAX_PAYLOAD_BYTES as u64 {
        return Err("CONFIG_LAST_GOOD_APPLY_EXISTING_TOO_LARGE".to_owned());
    }
    let mut file = File::open(path)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TARGET_READ_FAILED".to_owned())?;
    let mut value = Vec::with_capacity(metadata.len() as usize);
    file.read_to_end(&mut value)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TARGET_READ_FAILED".to_owned())?;
    Ok(value)
}

fn temporary_path(parent: &Path) -> PathBuf {
    let sequence = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    parent.join(format!(
        ".config-last-good.{}.{}.tmp",
        std::process::id(),
        sequence
    ))
}

#[cfg(unix)]
fn set_private_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    let mut permissions = fs::metadata(path)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TEMP_METADATA_FAILED".to_owned())?
        .permissions();
    permissions.set_mode(0o600);
    fs::set_permissions(path, permissions)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TEMP_MODE_FAILED".to_owned())
}

#[cfg(not(unix))]
fn set_private_permissions(_path: &Path) -> Result<(), String> {
    Ok(())
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<bool, String> {
    File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_DIRECTORY_SYNC_FAILED".to_owned())?;
    Ok(true)
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<bool, String> {
    Ok(false)
}

fn cleanup_directories(created: &[PathBuf]) {
    for path in created.iter().rev() {
        let _ = fs::remove_dir(path);
    }
}

pub fn apply_json(
    project_root: &str,
    expected_project_id: &str,
    config_wire: &[u8],
) -> Result<String, String> {
    let plan_text = plan_json(project_root, expected_project_id, config_wire)
        .map_err(|error| map_plan_error(&error))?;
    let plan: Value = serde_json::from_str(&plan_text)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PLAN_JSON_INVALID".to_owned())?;
    let decision = plan
        .get("decision")
        .and_then(Value::as_str)
        .ok_or_else(|| "CONFIG_LAST_GOOD_APPLY_DECISION_INVALID".to_owned())?;
    let project_id = plan
        .get("project_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "CONFIG_LAST_GOOD_APPLY_PROJECT_INVALID".to_owned())?;
    let payload = canonical_payload(config_wire)?;
    let payload_hash = sha256_hex(&payload);

    if decision == "retain-existing" {
        return serde_json::to_string(&json!({
            "action": "retained",
            "claim": CLAIM,
            "contract_id": CONTRACT_ID,
            "decision": decision,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
            "mutation": {
                "directory_created": false,
                "directory_synced": false,
                "lock_created": false,
                "target_replaced": false,
                "temporary_created": false,
            },
            "ok": true,
            "payload_bytes": payload.len(),
            "payload_sha256": payload_hash,
            "project_id": project_id,
            "schema_version": SCHEMA_VERSION,
            "target_sha256": Value::Null,
        }))
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_RESULT_JSON_INVALID".to_owned());
    }
    if decision != "write" {
        return Err("CONFIG_LAST_GOOD_APPLY_DECISION_INVALID".to_owned());
    }

    let root = Path::new(project_root);
    let target = root.join(TARGET_RELATIVE_PATH);
    let lock = root.join(LOCK_RELATIVE_PATH);
    let (parent, created) = ensure_secure_parent(root)?;
    let mut temporary: Option<PathBuf> = None;
    let mut lock_owned = false;
    let mut completed = false;
    let mut mutation = json!({
        "directory_created": !created.is_empty(),
        "directory_synced": false,
        "lock_created": false,
        "target_replaced": false,
        "temporary_created": false,
    });

    let operation = (|| -> Result<Value, String> {
        let target_exists = inspect_optional(
            &target,
            "CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK",
            "CONFIG_LAST_GOOD_APPLY_TARGET_TYPE_INVALID",
        )?;
        if fs::symlink_metadata(&lock).is_ok() {
            let metadata = fs::symlink_metadata(&lock)
                .map_err(|_| "CONFIG_LAST_GOOD_APPLY_PATH_INSPECTION_FAILED".to_owned())?;
            if metadata.file_type().is_symlink() {
                return Err("CONFIG_LAST_GOOD_APPLY_LOCK_SYMLINK".to_owned());
            }
            return Err("CONFIG_LAST_GOOD_APPLY_LOCK_BUSY".to_owned());
        }

        let mut lock_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock)
            .map_err(|error| {
                if error.kind() == std::io::ErrorKind::AlreadyExists {
                    "CONFIG_LAST_GOOD_APPLY_LOCK_BUSY".to_owned()
                } else {
                    "CONFIG_LAST_GOOD_APPLY_LOCK_CREATE_FAILED".to_owned()
                }
            })?;
        lock_owned = true;
        mutation["lock_created"] = Value::Bool(true);
        writeln!(lock_file, "{}", std::process::id())
            .and_then(|_| lock_file.sync_all())
            .map_err(|_| "CONFIG_LAST_GOOD_APPLY_LOCK_SYNC_FAILED".to_owned())?;

        if target_exists && read_bounded(&target)? == payload {
            completed = true;
            return Ok(json!({
                "action": "unchanged",
                "claim": CLAIM,
                "contract_id": CONTRACT_ID,
                "decision": decision,
                "full_product_parity": "FULL_PARITY_NOT_PROVEN",
                "mutation": mutation,
                "ok": true,
                "payload_bytes": payload.len(),
                "payload_sha256": payload_hash,
                "project_id": project_id,
                "schema_version": SCHEMA_VERSION,
                "target_sha256": payload_hash,
            }));
        }

        let path = temporary_path(&parent);
        let mut temp_file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TEMP_CREATE_FAILED".to_owned())?;
        temporary = Some(path.clone());
        mutation["temporary_created"] = Value::Bool(true);
        temp_file
            .write_all(&payload)
            .and_then(|_| temp_file.sync_all())
            .map_err(|_| "CONFIG_LAST_GOOD_APPLY_TEMP_SYNC_FAILED".to_owned())?;
        set_private_permissions(&path)?;
        inspect_optional(
            &target,
            "CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK",
            "CONFIG_LAST_GOOD_APPLY_TARGET_TYPE_INVALID",
        )?;
        fs::rename(&path, &target)
            .map_err(|_| "CONFIG_LAST_GOOD_APPLY_ATOMIC_REPLACE_FAILED".to_owned())?;
        temporary = None;
        mutation["target_replaced"] = Value::Bool(true);
        mutation["directory_synced"] = Value::Bool(sync_directory(&parent)?);
        let final_bytes = read_bounded(&target)?;
        if final_bytes != payload {
            return Err("CONFIG_LAST_GOOD_APPLY_POST_WRITE_MISMATCH".to_owned());
        }
        completed = true;
        Ok(json!({
            "action": "written",
            "claim": CLAIM,
            "contract_id": CONTRACT_ID,
            "decision": decision,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
            "mutation": mutation,
            "ok": true,
            "payload_bytes": payload.len(),
            "payload_sha256": payload_hash,
            "project_id": project_id,
            "schema_version": SCHEMA_VERSION,
            "target_sha256": sha256_hex(&final_bytes),
        }))
    })();

    if let Some(path) = temporary {
        let _ = fs::remove_file(path);
    }
    if lock_owned {
        let _ = fs::remove_file(&lock);
    }
    if !completed {
        cleanup_directories(&created);
    }

    let result = operation?;
    serde_json::to_string(&result)
        .map_err(|_| "CONFIG_LAST_GOOD_APPLY_RESULT_JSON_INVALID".to_owned())
}

#[cfg(test)]
mod tests {
    use super::canonical_payload;

    #[test]
    fn rejects_invalid_wire() {
        assert_eq!(
            canonical_payload(b"invalid"),
            Err("CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID".to_owned())
        );
    }
}
