#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use chacha20poly1305::aead::{AeadInPlace as _, KeyInit as _};
use chacha20poly1305::{Key, Tag, XChaCha20Poly1305, XNonce};
use serde_json::{json, Value};

use super::native_backup::{
    decode_environment_key, derive_key, initialize_evidence_state, initialize_roots, set_private,
    sha256_file, unique_temp_root,
};

const CHUNK_MAGIC: &[u8; 8] = b"SCCHNK2\0";
const MASTER_KEY_ENV: &str = "SYNTAVRA_EVIDENCE_MASTER_KEY_B64";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "backup" && action == "verify")
}

fn parse_arguments(arguments: &[String]) -> Result<(PathBuf, bool), String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "backup" && row[1] == "verify")
        .map(|index| index + 2)
        .ok_or_else(|| "BACKUP_VERIFY_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let plaintext = tail.iter().any(|value| value == "--plaintext");
    let path = tail
        .iter()
        .find(|value| !value.starts_with('-'))
        .ok_or_else(|| "BACKUP_VERIFY_PATH_MISSING".to_owned())?;
    let candidate = PathBuf::from(path);
    let absolute = if candidate.is_absolute() {
        candidate
    } else {
        env::current_dir()
            .map_err(|error| format!("BACKUP_VERIFY_CWD_FAILED:{error}"))?
            .join(candidate)
    };
    Ok((absolute, !plaintext))
}

fn read_exact_bytes<R: Read>(reader: &mut R, length: usize, code: &str) -> Result<Vec<u8>, String> {
    let mut output = vec![0_u8; length];
    reader
        .read_exact(&mut output)
        .map_err(|error| format!("{code}:{error}"))?;
    Ok(output)
}

fn key_for_id(state_root: &Path, key_id: &str) -> Result<[u8; 32], String> {
    if key_id == "env-v1" {
        let value = env::var(MASTER_KEY_ENV)
            .map_err(|_| format!("BACKUP_KEY_ENVIRONMENT_MISSING:{MASTER_KEY_ENV}"))?;
        if value.is_empty() {
            return Err(format!("BACKUP_KEY_ENVIRONMENT_MISSING:{MASTER_KEY_ENV}"));
        }
        return decode_environment_key(&value);
    }
    if key_id.is_empty()
        || key_id == "."
        || key_id == ".."
        || key_id.contains('/')
        || key_id.contains('\\')
    {
        return Err("BACKUP_KEY_ID_INVALID".to_owned());
    }
    let path = state_root
        .join("backup-keys/keys")
        .join(format!("{key_id}.key"));
    if !path.is_file() {
        return Err(format!("BACKUP_KEY_UNAVAILABLE:{key_id}"));
    }
    let raw = fs::read(path).map_err(|error| format!("BACKUP_KEY_READ_FAILED:{error}"))?;
    raw.try_into()
        .map_err(|_| "BACKUP_KEY_FILE_LENGTH_INVALID".to_owned())
}

pub(crate) fn open_sealed_file(
    source: &Path,
    destination: &Path,
    state_root: &Path,
    project_id: &str,
) -> Result<(), String> {
    let mut input = File::open(source).map_err(|error| format!("BACKUP_OPEN_FAILED:{error}"))?;
    let header = read_exact_bytes(&mut input, 29, "BACKUP_OPEN_HEADER_TRUNCATED")?;
    if &header[..8] != CHUNK_MAGIC {
        return Err("BACKUP_OPEN_HEADER_INVALID".to_owned());
    }
    let key_size = usize::from(header[8]);
    let chunk_bytes = u32::from_be_bytes(
        header[9..13]
            .try_into()
            .map_err(|_| "BACKUP_OPEN_HEADER_INVALID".to_owned())?,
    );
    let plaintext_size = u64::from_be_bytes(
        header[13..21]
            .try_into()
            .map_err(|_| "BACKUP_OPEN_HEADER_INVALID".to_owned())?,
    );
    let count = u64::from_be_bytes(
        header[21..29]
            .try_into()
            .map_err(|_| "BACKUP_OPEN_HEADER_INVALID".to_owned())?,
    );
    if key_size == 0 || chunk_bytes == 0 {
        return Err("BACKUP_OPEN_HEADER_INVALID".to_owned());
    }
    let encoded_key = read_exact_bytes(&mut input, key_size, "BACKUP_OPEN_KEY_ID_TRUNCATED")?;
    let key_id =
        std::str::from_utf8(&encoded_key).map_err(|_| "BACKUP_OPEN_KEY_ID_INVALID".to_owned())?;
    let master_key = key_for_id(state_root, key_id)?;
    let derived = derive_key(&master_key, project_id, key_id)?;
    let cipher = XChaCha20Poly1305::new(Key::from_slice(&derived));
    let mut output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(destination)
        .map_err(|error| format!("BACKUP_OPEN_DESTINATION_FAILED:{error}"))?;
    let mut seen = 0_u64;
    for expected_index in 0..count {
        let record = read_exact_bytes(&mut input, 36, "BACKUP_OPEN_RECORD_TRUNCATED")?;
        let index = u64::from_be_bytes(
            record[..8]
                .try_into()
                .map_err(|_| "BACKUP_OPEN_RECORD_INVALID".to_owned())?,
        );
        let nonce: [u8; 24] = record[8..32]
            .try_into()
            .map_err(|_| "BACKUP_OPEN_RECORD_INVALID".to_owned())?;
        let length = u32::from_be_bytes(
            record[32..36]
                .try_into()
                .map_err(|_| "BACKUP_OPEN_RECORD_INVALID".to_owned())?,
        );
        if index != expected_index || length > chunk_bytes {
            return Err("BACKUP_OPEN_RECORD_INVALID".to_owned());
        }
        let mut ciphertext = read_exact_bytes(
            &mut input,
            usize::try_from(length).map_err(|_| "BACKUP_OPEN_LENGTH_INVALID".to_owned())?,
            "BACKUP_OPEN_CHUNK_TRUNCATED",
        )?;
        let tag = read_exact_bytes(&mut input, 16, "BACKUP_OPEN_TAG_TRUNCATED")?;
        let mut aad = Vec::with_capacity(header.len() + encoded_key.len() + record.len());
        aad.extend_from_slice(&header);
        aad.extend_from_slice(&encoded_key);
        aad.extend_from_slice(&record);
        cipher
            .decrypt_in_place_detached(
                XNonce::from_slice(&nonce),
                &aad,
                &mut ciphertext,
                Tag::from_slice(&tag),
            )
            .map_err(|_| "BACKUP_OPEN_AUTHENTICATION_FAILED".to_owned())?;
        output
            .write_all(&ciphertext)
            .map_err(|error| format!("BACKUP_OPEN_WRITE_FAILED:{error}"))?;
        seen = seen
            .checked_add(u64::from(length))
            .ok_or_else(|| "BACKUP_OPEN_LENGTH_OVERFLOW".to_owned())?;
    }
    let mut trailing = [0_u8; 1];
    if seen != plaintext_size
        || input
            .read(&mut trailing)
            .map_err(|error| format!("BACKUP_OPEN_TRAILING_READ_FAILED:{error}"))?
            != 0
    {
        return Err("BACKUP_OPEN_LENGTH_MISMATCH".to_owned());
    }
    output
        .flush()
        .and_then(|_| output.sync_all())
        .map_err(|error| format!("BACKUP_OPEN_SYNC_FAILED:{error}"))?;
    set_private(destination);
    Ok(())
}

pub(crate) fn safe_path(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_) | Component::CurDir))
}

pub(crate) fn safe_extract(archive_path: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination)
        .map_err(|error| format!("BACKUP_EXTRACT_ROOT_FAILED:{error}"))?;
    let file =
        File::open(archive_path).map_err(|error| format!("BACKUP_ARCHIVE_OPEN_FAILED:{error}"))?;
    let mut archive = tar::Archive::new(file);
    for entry in archive
        .entries()
        .map_err(|error| format!("BACKUP_ARCHIVE_READ_FAILED:{error}"))?
    {
        let mut entry = entry.map_err(|error| format!("BACKUP_ARCHIVE_ENTRY_FAILED:{error}"))?;
        let relative = entry
            .path()
            .map_err(|error| format!("BACKUP_ARCHIVE_PATH_FAILED:{error}"))?
            .into_owned();
        if !safe_path(&relative) {
            return Err("BACKUP_ARCHIVE_PATH_TRAVERSAL".to_owned());
        }
        let target = destination.join(relative);
        let kind = entry.header().entry_type();
        if kind.is_dir() {
            fs::create_dir_all(target)
                .map_err(|error| format!("BACKUP_EXTRACT_DIRECTORY_FAILED:{error}"))?;
            continue;
        }
        if !kind.is_file() {
            return Err("BACKUP_ARCHIVE_SPECIAL_FILE_UNSUPPORTED".to_owned());
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("BACKUP_EXTRACT_PARENT_FAILED:{error}"))?;
        }
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&target)
            .map_err(|error| format!("BACKUP_EXTRACT_FILE_FAILED:{error}"))?;
        std::io::copy(&mut entry, &mut output)
            .map_err(|error| format!("BACKUP_EXTRACT_COPY_FAILED:{error}"))?;
        output
            .flush()
            .and_then(|_| output.sync_all())
            .map_err(|error| format!("BACKUP_EXTRACT_SYNC_FAILED:{error}"))?;
    }
    Ok(())
}

pub(crate) fn verify_extracted(extracted: &Path, project_id: &str) -> Result<Value, String> {
    let manifest_path = extracted.join("BACKUP_MANIFEST.json");
    if !manifest_path.is_file() {
        return Err("BACKUP_MANIFEST_MISSING".to_owned());
    }
    let manifest = serde_json::from_slice::<Value>(
        &fs::read(manifest_path).map_err(|error| format!("BACKUP_MANIFEST_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("BACKUP_MANIFEST_INVALID:{error}"))?;
    if manifest["project_id"].as_str() != Some(project_id) {
        return Err("BACKUP_PROJECT_SCOPE_MISMATCH".to_owned());
    }
    let files = manifest["files"]
        .as_object()
        .ok_or_else(|| "BACKUP_MANIFEST_FILES_INVALID".to_owned())?;
    let mut failures = Vec::new();
    for (relative, expected) in files {
        let relative_path = Path::new(relative);
        if !safe_path(relative_path) {
            failures.push(format!("{relative}:escape"));
            continue;
        }
        let path = extracted.join(relative_path);
        let expected_hash = expected.get("sha256").and_then(Value::as_str);
        let matches = if path.is_file() {
            match (sha256_file(&path), expected_hash) {
                (Ok(actual), Some(expected)) => actual == expected,
                _ => false,
            }
        } else {
            false
        };
        if !matches {
            failures.push(relative.clone());
        }
    }
    Ok(json!({
        "ok": failures.is_empty(),
        "files": files.len(),
        "failures": failures,
    }))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    initialize_evidence_state(state_root)?;
    initialize_roots(state_root)?;
    let (source, encrypted) = parse_arguments(arguments)?;
    if !source.is_file() {
        return Err("BACKUP_FILE_MISSING".to_owned());
    }
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let temporary = unique_temp_root()?;
    let result = (|| -> Result<Value, String> {
        let archive = if encrypted {
            let path = temporary.join("state.tar");
            open_sealed_file(&source, &path, state_root, &project_id)?;
            path
        } else {
            source
        };
        let extracted = temporary.join("extracted");
        safe_extract(&archive, &extracted)?;
        verify_extracted(&extracted, &project_id)
    })();
    let _ = fs::remove_dir_all(temporary);
    result
}

#[cfg(test)]
mod tests {
    use super::{safe_path, supports};
    use std::path::Path;

    #[test]
    fn routes_backup_verify_only() {
        assert!(supports(&["backup".to_owned(), "verify".to_owned()]));
        assert!(!supports(&["backup".to_owned(), "create".to_owned()]));
    }

    #[test]
    fn rejects_escaping_archive_paths() {
        assert!(safe_path(Path::new("state/file.json")));
        assert!(!safe_path(Path::new("../escape")));
        assert!(!safe_path(Path::new("/absolute")));
    }
}
