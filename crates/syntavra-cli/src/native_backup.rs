#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::BTreeMap;
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use chacha20poly1305::aead::{AeadInPlace as _, KeyInit as _};
use chacha20poly1305::{Key, XChaCha20Poly1305, XNonce};
use hkdf::Hkdf;
use rand::{rngs::OsRng, RngCore as _};
use rusqlite::backup::Backup;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

const CHUNK_MAGIC: &[u8; 8] = b"SCCHNK2\0";
const DEFAULT_CHUNK_BYTES: usize = 1024 * 1024;
const MASTER_KEY_ENV: &str = "SYNTAVRA_EVIDENCE_MASTER_KEY_B64";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "backup" && action == "create")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "backup" && row[1] == "create")
        .map(|index| index + 2)
        .ok_or_else(|| "BACKUP_CREATE_COMMAND_MISSING".to_owned())
}

fn parse_arguments(arguments: &[String]) -> Result<(PathBuf, bool), String> {
    let start = command_start(arguments)?;
    let tail = &arguments[start..];
    let plaintext = tail.iter().any(|value| value == "--plaintext");
    let path = tail
        .iter()
        .find(|value| !value.starts_with('-'))
        .ok_or_else(|| "BACKUP_CREATE_PATH_MISSING".to_owned())?;
    let candidate = PathBuf::from(path);
    let absolute = if candidate.is_absolute() {
        candidate
    } else {
        env::current_dir()
            .map_err(|error| format!("BACKUP_CREATE_CWD_FAILED:{error}"))?
            .join(candidate)
    };
    Ok((absolute, !plaintext))
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("BACKUP_CLOCK_FAILED:{error}"))
}

fn unique_temp_root() -> Result<PathBuf, String> {
    let mut random = [0_u8; 12];
    OsRng.fill_bytes(&mut random);
    let root = env::temp_dir().join(format!(
        "syntavra-backup-{}-{}",
        std::process::id(),
        hex(&random)
    ));
    fs::create_dir_all(&root).map_err(|error| format!("BACKUP_TEMP_CREATE_FAILED:{error}"))?;
    Ok(root)
}

fn hex(bytes: &[u8]) -> String {
    let mut rendered = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut rendered, "{byte:02x}").expect("writing to String cannot fail");
    }
    rendered
}

fn sha256_bytes(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|error| format!("BACKUP_HASH_OPEN_FAILED:{error}"))?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| format!("BACKUP_HASH_READ_FAILED:{error}"))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(hex(&digest.finalize()))
}

fn set_private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        if let Ok(metadata) = fs::metadata(path) {
            let mut permissions = metadata.permissions();
            permissions.set_mode(0o600);
            let _ = fs::set_permissions(path, permissions);
        }
    }
    #[cfg(not(unix))]
    let _ = path;
}

fn atomic_write(path: &Path, payload: &[u8], private: bool) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "BACKUP_ATOMIC_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("BACKUP_ATOMIC_PARENT_CREATE_FAILED:{error}"))?;
    let mut random = [0_u8; 8];
    OsRng.fill_bytes(&mut random);
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "BACKUP_ATOMIC_NAME_INVALID".to_owned())?;
    let temporary = parent.join(format!(".{name}.tmp-{}", hex(&random)));
    let result = (|| -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(payload)?;
        file.flush()?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
        return Err(format!(
            "BACKUP_ATOMIC_WRITE_FAILED:{}",
            result.unwrap_err()
        ));
    }
    if private {
        set_private(path);
    }
    Ok(())
}

fn collect_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    fn visit(root: &Path, current: &Path, output: &mut Vec<PathBuf>) -> Result<(), String> {
        let mut children = fs::read_dir(current)
            .map_err(|error| format!("BACKUP_STATE_ENUMERATE_FAILED:{error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BACKUP_STATE_ENTRY_FAILED:{error}"))?;
        children.sort_by_key(std::fs::DirEntry::file_name);
        for child in children {
            let path = child.path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("BACKUP_STATE_METADATA_FAILED:{error}"))?;
            if metadata.file_type().is_symlink() {
                continue;
            }
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "BACKUP_STATE_SCOPE_FAILED".to_owned())?;
            if relative
                .components()
                .next()
                .and_then(|value| value.as_os_str().to_str())
                .is_some_and(|value| matches!(value, "backups" | "backup-keys" | "tmp"))
            {
                continue;
            }
            if metadata.is_dir() {
                visit(root, &path, output)?;
            } else if metadata.is_file() {
                output.push(path);
            }
        }
        Ok(())
    }

    let mut files = Vec::new();
    if root.is_dir() {
        visit(root, root, &mut files)?;
    }
    files.sort_by_key(|path| {
        path.strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/")
    });
    Ok(files)
}

fn is_sqlite(path: &Path) -> bool {
    path.extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| matches!(value, "sqlite" | "sqlite3" | "db"))
}

fn copy_permissions(source: &Path, target: &Path) {
    if let Ok(metadata) = fs::metadata(source) {
        let _ = fs::set_permissions(target, metadata.permissions());
    }
}

fn backup_sqlite(source: &Path, target: &Path) -> Result<(), String> {
    let attempt = (|| -> Result<(), rusqlite::Error> {
        let source_connection = Connection::open_with_flags(
            source,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
        )?;
        let mut target_connection = Connection::open(target)?;
        let backup = Backup::new(&source_connection, &mut target_connection)?;
        backup.run_to_completion(5, Duration::from_millis(0), None)?;
        let integrity = target_connection.query_row("PRAGMA integrity_check", [], |row| {
            row.get::<_, String>(0)
        })?;
        if integrity != "ok" {
            return Err(rusqlite::Error::InvalidQuery);
        }
        Ok(())
    })();
    if attempt.is_err() {
        fs::copy(source, target)
            .map_err(|error| format!("BACKUP_SQLITE_FALLBACK_COPY_FAILED:{error}"))?;
    }
    copy_permissions(source, target);
    Ok(())
}

fn copy_state_file(source: &Path, target: &Path) -> Result<(), String> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("BACKUP_STAGING_PARENT_FAILED:{error}"))?;
    }
    if is_sqlite(source) {
        backup_sqlite(source, target)
    } else {
        fs::copy(source, target)
            .map_err(|error| format!("BACKUP_STATE_COPY_FAILED:{error}"))?;
        copy_permissions(source, target);
        Ok(())
    }
}

fn build_manifest(
    state_root: &Path,
    staging: &Path,
    project_id: &str,
    created_at: f64,
) -> Result<Value, String> {
    let mut files = BTreeMap::<String, Value>::new();
    for source in collect_files(state_root)? {
        let relative = source
            .strip_prefix(state_root)
            .map_err(|_| "BACKUP_RELATIVE_PATH_FAILED".to_owned())?;
        let target = staging.join(relative);
        copy_state_file(&source, &target)?;
        files.insert(
            relative.to_string_lossy().replace('\\', "/"),
            json!({
                "sha256": sha256_file(&target)?,
                "bytes": fs::metadata(&target)
                    .map_err(|error| format!("BACKUP_STAGING_METADATA_FAILED:{error}"))?
                    .len(),
            }),
        );
    }
    Ok(json!({
        "schema_version": 1,
        "project_id": project_id,
        "created_at": created_at,
        "files": files,
    }))
}

fn write_manifest(staging: &Path, manifest: &Value) -> Result<PathBuf, String> {
    let path = staging.join("BACKUP_MANIFEST.json");
    let mut payload = serde_json::to_vec(manifest)
        .map_err(|error| format!("BACKUP_MANIFEST_SERIALIZE_FAILED:{error}"))?;
    payload.push(b'\n');
    atomic_write(&path, &payload, true)?;
    Ok(path)
}

fn collect_archive_entries(staging: &Path) -> Result<Vec<PathBuf>, String> {
    fn visit(current: &Path, output: &mut Vec<PathBuf>) -> Result<(), String> {
        let mut children = fs::read_dir(current)
            .map_err(|error| format!("BACKUP_ARCHIVE_ENUMERATE_FAILED:{error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BACKUP_ARCHIVE_ENTRY_FAILED:{error}"))?;
        children.sort_by_key(std::fs::DirEntry::file_name);
        for child in children {
            let path = child.path();
            output.push(path.clone());
            if path.is_dir() {
                visit(&path, output)?;
            }
        }
        Ok(())
    }
    let mut entries = Vec::new();
    visit(staging, &mut entries)?;
    entries.sort_by_key(|path| {
        path.strip_prefix(staging)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/")
    });
    Ok(entries)
}

fn create_tar(staging: &Path, archive: &Path) -> Result<(), String> {
    let file = File::create(archive)
        .map_err(|error| format!("BACKUP_ARCHIVE_CREATE_FAILED:{error}"))?;
    let mut builder = tar::Builder::new(file);
    for path in collect_archive_entries(staging)? {
        let relative = path
            .strip_prefix(staging)
            .map_err(|_| "BACKUP_ARCHIVE_SCOPE_FAILED".to_owned())?;
        if path.is_dir() {
            builder
                .append_dir(relative, &path)
                .map_err(|error| format!("BACKUP_ARCHIVE_DIRECTORY_FAILED:{error}"))?;
        } else if path.is_file() {
            builder
                .append_path_with_name(&path, relative)
                .map_err(|error| format!("BACKUP_ARCHIVE_FILE_FAILED:{error}"))?;
        }
    }
    builder
        .finish()
        .map_err(|error| format!("BACKUP_ARCHIVE_FINISH_FAILED:{error}"))?;
    Ok(())
}

struct ActiveKey {
    key_id: String,
    key: [u8; 32],
}

fn decode_environment_key(value: &str) -> Result<[u8; 32], String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|error| format!("BACKUP_KEY_BASE64_INVALID:{error}"))?;
    bytes
        .try_into()
        .map_err(|_| "BACKUP_KEY_LENGTH_INVALID".to_owned())
}

fn active_key(state_root: &Path) -> Result<ActiveKey, String> {
    if let Ok(value) = env::var(MASTER_KEY_ENV) {
        if !value.is_empty() {
            return Ok(ActiveKey {
                key_id: "env-v1".to_owned(),
                key: decode_environment_key(&value)?,
            });
        }
    }
    let keys = state_root.join("backup-keys/keys");
    fs::create_dir_all(&keys)
        .map_err(|error| format!("BACKUP_KEY_DIRECTORY_FAILED:{error}"))?;
    let registry_path = keys.join("registry.json");
    let key_id = if registry_path.is_file() {
        let value = serde_json::from_slice::<Value>(
            &fs::read(&registry_path)
                .map_err(|error| format!("BACKUP_KEY_REGISTRY_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("BACKUP_KEY_REGISTRY_INVALID:{error}"))?;
        if value["schema_version"].as_u64() != Some(1) {
            return Err("BACKUP_KEY_REGISTRY_VERSION_INVALID".to_owned());
        }
        value["active"]
            .as_str()
            .unwrap_or("local-v1")
            .to_owned()
    } else {
        "local-v1".to_owned()
    };
    let key_path = keys.join(format!("{key_id}.key"));
    if !key_path.is_file() {
        let mut key = [0_u8; 32];
        OsRng.fill_bytes(&mut key);
        atomic_write(&key_path, &key, true)?;
        let registry = json!({
            "schema_version": 1,
            "active": key_id,
            "keys": [key_id],
        });
        let payload = serde_json::to_vec(&registry)
            .map_err(|error| format!("BACKUP_KEY_REGISTRY_SERIALIZE_FAILED:{error}"))?;
        atomic_write(&registry_path, &payload, true)?;
    }
    let raw = fs::read(&key_path)
        .map_err(|error| format!("BACKUP_KEY_READ_FAILED:{error}"))?;
    let key = raw
        .try_into()
        .map_err(|_| "BACKUP_KEY_FILE_LENGTH_INVALID".to_owned())?;
    Ok(ActiveKey { key_id, key })
}

fn derive_key(master_key: &[u8; 32], project_id: &str, key_id: &str) -> Result<[u8; 32], String> {
    let salt = Sha256::digest(format!("syntavra:{project_id}").as_bytes());
    let hkdf = Hkdf::<Sha256>::new(Some(&salt), master_key);
    let mut output = [0_u8; 32];
    hkdf.expand(
        format!("evidence-xchacha20poly1305:{key_id}").as_bytes(),
        &mut output,
    )
    .map_err(|_| "BACKUP_HKDF_EXPAND_FAILED".to_owned())?;
    Ok(output)
}

fn chunk_header(key_size: u8, chunk_bytes: u32, plaintext_size: u64, count: u64) -> Vec<u8> {
    let mut output = Vec::with_capacity(29);
    output.extend_from_slice(CHUNK_MAGIC);
    output.push(key_size);
    output.extend_from_slice(&chunk_bytes.to_be_bytes());
    output.extend_from_slice(&plaintext_size.to_be_bytes());
    output.extend_from_slice(&count.to_be_bytes());
    output
}

fn chunk_record(index: u64, nonce: &[u8; 24], length: u32) -> Vec<u8> {
    let mut output = Vec::with_capacity(36);
    output.extend_from_slice(&index.to_be_bytes());
    output.extend_from_slice(nonce);
    output.extend_from_slice(&length.to_be_bytes());
    output
}

fn seal_file(
    source: &Path,
    destination: &Path,
    project_id: &str,
    active: &ActiveKey,
) -> Result<(), String> {
    let size = fs::metadata(source)
        .map_err(|error| format!("BACKUP_SEAL_SOURCE_METADATA_FAILED:{error}"))?
        .len();
    let count = if size == 0 {
        0
    } else {
        size.div_ceil(DEFAULT_CHUNK_BYTES as u64)
    };
    let encoded_key = active.key_id.as_bytes();
    let key_size = u8::try_from(encoded_key.len())
        .map_err(|_| "BACKUP_SEAL_KEY_ID_TOO_LONG".to_owned())?;
    if key_size == 0 {
        return Err("BACKUP_SEAL_KEY_ID_EMPTY".to_owned());
    }
    let header = chunk_header(
        key_size,
        DEFAULT_CHUNK_BYTES as u32,
        size,
        count,
    );
    let derived = derive_key(&active.key, project_id, &active.key_id)?;
    let cipher = XChaCha20Poly1305::new(Key::from_slice(&derived));
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("BACKUP_SEAL_PARENT_FAILED:{error}"))?;
    }
    let mut input = File::open(source)
        .map_err(|error| format!("BACKUP_SEAL_SOURCE_OPEN_FAILED:{error}"))?;
    let mut output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(destination)
        .map_err(|error| format!("BACKUP_SEAL_DESTINATION_OPEN_FAILED:{error}"))?;
    output
        .write_all(&header)
        .and_then(|_| output.write_all(encoded_key))
        .map_err(|error| format!("BACKUP_SEAL_HEADER_WRITE_FAILED:{error}"))?;
    for index in 0..count {
        let mut plaintext = vec![0_u8; DEFAULT_CHUNK_BYTES];
        let read = input
            .read(&mut plaintext)
            .map_err(|error| format!("BACKUP_SEAL_READ_FAILED:{error}"))?;
        plaintext.truncate(read);
        let mut nonce = [0_u8; 24];
        OsRng.fill_bytes(&mut nonce);
        let record = chunk_record(index, &nonce, read as u32);
        let mut aad = Vec::with_capacity(header.len() + encoded_key.len() + record.len());
        aad.extend_from_slice(&header);
        aad.extend_from_slice(encoded_key);
        aad.extend_from_slice(&record);
        let tag = cipher
            .encrypt_in_place_detached(XNonce::from_slice(&nonce), &aad, &mut plaintext)
            .map_err(|_| "BACKUP_SEAL_ENCRYPT_FAILED".to_owned())?;
        output
            .write_all(&record)
            .and_then(|_| output.write_all(&plaintext))
            .and_then(|_| output.write_all(tag.as_slice()))
            .map_err(|error| format!("BACKUP_SEAL_CHUNK_WRITE_FAILED:{error}"))?;
    }
    output
        .flush()
        .and_then(|_| output.sync_all())
        .map_err(|error| format!("BACKUP_SEAL_SYNC_FAILED:{error}"))?;
    set_private(destination);
    Ok(())
}

fn initialize_roots(state_root: &Path) -> Result<(), String> {
    fs::create_dir_all(state_root.join("backups"))
        .map_err(|error| format!("BACKUP_ROOT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root.join("backup-keys/keys"))
        .map_err(|error| format!("BACKUP_KEY_ROOT_CREATE_FAILED:{error}"))?;
    Ok(())
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    initialize_roots(state_root)?;
    let (destination, encrypt) = parse_arguments(arguments)?;
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("BACKUP_DESTINATION_PARENT_FAILED:{error}"))?;
    }
    let project_id = super::state_snapshot_contract::project_id_for_root(
        &project_root.to_string_lossy(),
    )?;
    let created_at = now_seconds()?;
    let temporary = unique_temp_root()?;
    let result = (|| -> Result<Value, String> {
        let staging = temporary.join("state");
        fs::create_dir_all(&staging)
            .map_err(|error| format!("BACKUP_STAGING_CREATE_FAILED:{error}"))?;
        let manifest = build_manifest(state_root, &staging, &project_id, created_at)?;
        let manifest_path = write_manifest(&staging, &manifest)?;
        let archive = temporary.join("state.tar");
        create_tar(&staging, &archive)?;
        if encrypt {
            let active = active_key(state_root)?;
            let temporary_destination = destination.with_file_name(format!(
                "{}.tmp",
                destination
                    .file_name()
                    .and_then(|value| value.to_str())
                    .ok_or_else(|| "BACKUP_DESTINATION_NAME_INVALID".to_owned())?
            ));
            let _ = fs::remove_file(&temporary_destination);
            seal_file(&archive, &temporary_destination, &project_id, &active)?;
            fs::rename(&temporary_destination, &destination)
                .map_err(|error| format!("BACKUP_DESTINATION_REPLACE_FAILED:{error}"))?;
        } else {
            fs::copy(&archive, &destination)
                .map_err(|error| format!("BACKUP_ARCHIVE_COPY_FAILED:{error}"))?;
        }
        Ok(json!({
            "path": destination.to_string_lossy(),
            "files": manifest["files"].as_object().map_or(0, serde_json::Map::len),
            "plaintext_bytes": fs::metadata(&archive)
                .map_err(|error| format!("BACKUP_ARCHIVE_METADATA_FAILED:{error}"))?
                .len(),
            "encrypted": encrypt,
            "created_at": created_at,
            "manifest_hash": sha256_file(&manifest_path)?,
        }))
    })();
    let _ = fs::remove_dir_all(&temporary);
    result
}

#[cfg(test)]
mod tests {
    use super::{chunk_header, chunk_record, supports};

    #[test]
    fn chunk_binary_layout_matches_python_structs() {
        assert_eq!(chunk_header(8, 1_048_576, 12, 1).len(), 29);
        assert_eq!(chunk_record(0, &[0_u8; 24], 12).len(), 36);
    }

    #[test]
    fn routes_backup_create_only() {
        assert!(supports(&["backup".to_owned(), "create".to_owned()]));
        assert!(!supports(&["backup".to_owned(), "verify".to_owned()]));
    }
}
