#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use aes_gcm::aead::{AeadInPlace as _, KeyInit as _};
use aes_gcm::{Aes256Gcm, Nonce, Tag};
use hkdf::Hkdf;
use rand::{rngs::OsRng, RngCore as _};
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};
use zeroize::Zeroize;

use super::native_evidence_store::NativeEvidenceStore;

const MAGIC: &[u8; 6] = b"SCEV1\0";
const NONCE_BYTES: usize = 12;
const TAG_BYTES: usize = 16;
const KEY_BYTES: usize = 32;
const SCHEMA_VERSION: u64 = 3;
const MANAGED_KEY_ENV: &str = "SYNTAVRA_EVIDENCE_KEY";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && matches!(action.as_str(), "get" | "rotate-key"))
}

#[derive(Debug, PartialEq, Eq)]
struct GetArguments {
    handle: String,
    max_bytes: Option<i128>,
    output: Option<String>,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("EVIDENCE_OPTION_VALUE_MISSING:{option}"))
}

fn parse_max_bytes(value: &str) -> Result<i128, String> {
    value
        .parse::<i128>()
        .map_err(|error| format!("EVIDENCE_MAX_BYTES_INVALID:{error}"))
}

fn parse_arguments(arguments: &[String]) -> Result<GetArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "evidence" && row[1] == "get")
        .map(|index| index + 2)
        .ok_or_else(|| "EVIDENCE_GET_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut handle = None;
    let mut max_bytes = None;
    let mut output = None;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if value == "--max-bytes" {
            max_bytes = Some(parse_max_bytes(&next_value(
                tail,
                &mut index,
                "--max-bytes",
            )?)?);
        } else if let Some(value) = value.strip_prefix("--max-bytes=") {
            max_bytes = Some(parse_max_bytes(value)?);
        } else if value == "--output" {
            output = Some(next_value(tail, &mut index, "--output")?);
        } else if let Some(value) = value.strip_prefix("--output=") {
            output = Some(value.to_owned());
        } else if value.starts_with('-') {
            return Err(format!("EVIDENCE_OPTION_UNKNOWN:{value}"));
        } else if handle.replace(value.clone()).is_some() {
            return Err(format!("EVIDENCE_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }
    Ok(GetArguments {
        handle: handle.ok_or_else(|| "EVIDENCE_HANDLE_MISSING".to_owned())?,
        max_bytes,
        output,
    })
}

fn write_output(path: &Path, data: &[u8]) -> Result<(), String> {
    fs::write(path, data).map_err(|error| format!("EVIDENCE_OUTPUT_WRITE_FAILED:{error}"))
}

fn is_rotate_key(arguments: &[String]) -> bool {
    arguments
        .windows(2)
        .any(|row| row[0] == "evidence" && row[1] == "rotate-key")
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("EVIDENCE_CLOCK_FAILED:{error}"))
}

fn hex(bytes: &[u8]) -> String {
    let mut rendered = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut rendered, "{byte:02x}").expect("writing to String cannot fail");
    }
    rendered
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("EVIDENCE_JSON_SERIALIZE_FAILED:{error}"))
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
}

fn atomic_write(path: &Path, payload: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "EVIDENCE_ATOMIC_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("EVIDENCE_ATOMIC_PARENT_CREATE_FAILED:{error}"))?;
    let mut random = [0_u8; 12];
    OsRng.fill_bytes(&mut random);
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "EVIDENCE_ATOMIC_NAME_INVALID".to_owned())?;
    let temporary = parent.join(format!(".{name}.{}", hex(&random)));
    let result = (|| -> std::io::Result<()> {
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        output.write_all(payload)?;
        output.flush()?;
        output.sync_all()?;
        #[cfg(windows)]
        if path.exists() {
            fs::remove_file(path)?;
        }
        fs::rename(&temporary, path)?;
        Ok(())
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("EVIDENCE_ATOMIC_WRITE_FAILED:{error}"));
    }
    set_private(path);
    Ok(())
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    let mut payload = canonical_json(value)?;
    payload.push(b'\n');
    atomic_write(path, &payload)
}

struct RotationStore {
    root: PathBuf,
    project_id: String,
    active_version: u32,
}

impl RotationStore {
    fn open(state_root: &Path, project_id: &str) -> Result<Self, String> {
        let _ = NativeEvidenceStore::open(state_root, project_id)?;
        let root = state_root.join("evidence");
        let active = serde_json::from_slice::<Value>(
            &fs::read(root.join("keys/active.json"))
                .map_err(|error| format!("EVIDENCE_ACTIVE_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_ACTIVE_INVALID:{error}"))?;
        let active_version = active["active_version"]
            .as_u64()
            .and_then(|value| u32::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| "EVIDENCE_ACTIVE_VERSION_INVALID".to_owned())?;
        Ok(Self {
            root,
            project_id: project_id.to_owned(),
            active_version,
        })
    }

    fn object_path(&self, digest: &str) -> PathBuf {
        self.root
            .join("objects")
            .join(&digest[..2])
            .join(&digest[2..])
    }

    fn metadata_path(&self, digest: &str) -> PathBuf {
        self.root.join("metadata").join(format!("{digest}.json"))
    }

    fn master_key(&self, version: u32) -> Result<[u8; KEY_BYTES], String> {
        if version == 0 {
            return Err("EVIDENCE_KEY_VERSION_INVALID".to_owned());
        }
        let path = self
            .root
            .join("keys")
            .join(format!("master-v{version}.key"));
        let raw = fs::read(path).map_err(|error| format!("EVIDENCE_KEY_READ_FAILED:{error}"))?;
        raw.try_into()
            .map_err(|_| "EVIDENCE_KEY_FILE_LENGTH_INVALID".to_owned())
    }

    fn data_key(&self, version: u32) -> Result<[u8; KEY_BYTES], String> {
        let mut master = self.master_key(version)?;
        let salt = Sha256::digest(format!("syntavra:evidence:{}", self.project_id).as_bytes());
        let hkdf = Hkdf::<Sha256>::new(Some(&salt), &master);
        let mut output = [0_u8; KEY_BYTES];
        let result = hkdf.expand(
            format!("syntavra-evidence-v{version}").as_bytes(),
            &mut output,
        );
        master.zeroize();
        result.map_err(|_| "EVIDENCE_HKDF_EXPAND_FAILED".to_owned())?;
        Ok(output)
    }

    fn aad(&self, digest: &str, version: u32) -> Result<Vec<u8>, String> {
        let mut value = BTreeMap::<String, Value>::new();
        value.insert("digest".to_owned(), Value::String(digest.to_owned()));
        value.insert("key_version".to_owned(), Value::from(version));
        value.insert(
            "project_id".to_owned(),
            Value::String(self.project_id.clone()),
        );
        value.insert("schema".to_owned(), Value::from(SCHEMA_VERSION));
        canonical_json(
            &serde_json::to_value(value)
                .map_err(|error| format!("EVIDENCE_AAD_VALUE_FAILED:{error}"))?,
        )
    }

    fn decrypt_digest(&self, digest: &str) -> Result<Vec<u8>, String> {
        let payload = fs::read(self.object_path(digest))
            .map_err(|error| format!("EVIDENCE_OBJECT_READ_FAILED:{error}"))?;
        let minimum = MAGIC.len() + 4 + NONCE_BYTES + TAG_BYTES;
        if payload.len() < minimum || &payload[..MAGIC.len()] != MAGIC {
            return Err("EVIDENCE_OBJECT_HEADER_INVALID".to_owned());
        }
        let version = u32::from_be_bytes(
            payload[MAGIC.len()..MAGIC.len() + 4]
                .try_into()
                .map_err(|_| "EVIDENCE_OBJECT_VERSION_INVALID".to_owned())?,
        );
        let nonce_start = MAGIC.len() + 4;
        let nonce_end = nonce_start + NONCE_BYTES;
        let tag_start = payload.len() - TAG_BYTES;
        let nonce = &payload[nonce_start..nonce_end];
        let tag = &payload[tag_start..];
        let mut plaintext = payload[nonce_end..tag_start].to_vec();
        let mut key = self.data_key(version)?;
        let cipher = Aes256Gcm::new_from_slice(&key)
            .map_err(|_| "EVIDENCE_CIPHER_KEY_INVALID".to_owned())?;
        let aad = self.aad(digest, version)?;
        let result = cipher.decrypt_in_place_detached(
            Nonce::from_slice(nonce),
            &aad,
            &mut plaintext,
            Tag::from_slice(tag),
        );
        key.zeroize();
        result.map_err(|_| "EVIDENCE_AUTHENTICATION_FAILED".to_owned())?;
        let actual = hex(&Sha256::digest(&plaintext));
        if actual != digest {
            plaintext.zeroize();
            return Err("EVIDENCE_PLAINTEXT_DIGEST_MISMATCH".to_owned());
        }
        Ok(plaintext)
    }

    fn encrypt_object(
        &self,
        data: &[u8],
        destination: &Path,
        digest: &str,
        version: u32,
    ) -> Result<u64, String> {
        let mut key = self.data_key(version)?;
        let cipher = Aes256Gcm::new_from_slice(&key)
            .map_err(|_| "EVIDENCE_CIPHER_KEY_INVALID".to_owned())?;
        let mut nonce_bytes = [0_u8; NONCE_BYTES];
        OsRng.fill_bytes(&mut nonce_bytes);
        let mut ciphertext = data.to_vec();
        let aad = self.aad(digest, version)?;
        let tag = cipher
            .encrypt_in_place_detached(Nonce::from_slice(&nonce_bytes), &aad, &mut ciphertext)
            .map_err(|_| "EVIDENCE_ENCRYPT_FAILED".to_owned())?;
        key.zeroize();
        let mut payload =
            Vec::with_capacity(MAGIC.len() + 4 + NONCE_BYTES + ciphertext.len() + TAG_BYTES);
        payload.extend_from_slice(MAGIC);
        payload.extend_from_slice(&version.to_be_bytes());
        payload.extend_from_slice(&nonce_bytes);
        payload.extend_from_slice(&ciphertext);
        payload.extend_from_slice(tag.as_ref());
        atomic_write(destination, &payload)?;
        u64::try_from(payload.len()).map_err(|_| "EVIDENCE_STORED_SIZE_INVALID".to_owned())
    }

    fn rotation_integer(value: &Value, field: &str) -> Result<i128, String> {
        match value {
            Value::Bool(flag) => Ok(if *flag { 1 } else { 0 }),
            Value::Number(number) => number
                .as_i64()
                .map(i128::from)
                .or_else(|| number.as_u64().map(i128::from))
                .or_else(|| {
                    number
                        .as_f64()
                        .filter(|item| item.is_finite())
                        .map(|item| item.trunc() as i128)
                })
                .ok_or_else(|| format!("EVIDENCE_ROTATION_INTEGER_INVALID:{field}")),
            Value::String(text) => text
                .trim()
                .parse::<i128>()
                .map_err(|_| format!("EVIDENCE_ROTATION_INTEGER_INVALID:{field}")),
            _ => Err(format!("EVIDENCE_ROTATION_INTEGER_INVALID:{field}")),
        }
    }

    fn rotate_local_key(&mut self) -> Result<(u32, u32), String> {
        if env::var(MANAGED_KEY_ENV)
            .ok()
            .is_some_and(|value| !value.trim().is_empty())
        {
            return Err("EVIDENCE_MANAGED_KEY_ROTATION_FORBIDDEN".to_owned());
        }
        let previous = self.active_version;
        let active = previous
            .checked_add(1)
            .ok_or_else(|| "EVIDENCE_KEY_VERSION_OVERFLOW".to_owned())?;
        let key_path = self.root.join("keys").join(format!("master-v{active}.key"));
        if key_path.exists() {
            let size = fs::metadata(&key_path)
                .map_err(|error| format!("EVIDENCE_KEY_METADATA_FAILED:{error}"))?
                .len();
            if size != KEY_BYTES as u64 {
                return Err("EVIDENCE_KEY_FILE_LENGTH_INVALID".to_owned());
            }
        } else {
            let mut key = [0_u8; KEY_BYTES];
            OsRng.fill_bytes(&mut key);
            let result = atomic_write(&key_path, &key);
            key.zeroize();
            result?;
        }
        atomic_write_json(
            &self.root.join("keys/active.json"),
            &json!({
                "schema_version": 1,
                "active_version": active,
                "rotated_at": now_seconds()?,
            }),
        )?;
        self.active_version = active;
        Ok((previous, active))
    }

    fn rotation_rows(&self) -> Result<Vec<(String, u32, u64)>, String> {
        let connection = Connection::open(self.root.join("evidence.sqlite3"))
            .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
        let mut statement = connection
            .prepare("SELECT digest,key_version,stored_bytes FROM evidence_objects ORDER BY digest")
            .map_err(|error| format!("EVIDENCE_ROTATION_QUERY_PREPARE_FAILED:{error}"))?;
        let mut query = statement
            .query([])
            .map_err(|error| format!("EVIDENCE_ROTATION_QUERY_FAILED:{error}"))?;
        let mut rows = Vec::new();
        while let Some(row) = query
            .next()
            .map_err(|error| format!("EVIDENCE_ROTATION_ROW_FAILED:{error}"))?
        {
            let digest = row
                .get::<_, String>(0)
                .map_err(|error| format!("EVIDENCE_ROTATION_DIGEST_INVALID:{error}"))?;
            let key_version = row
                .get::<_, i64>(1)
                .map_err(|error| format!("EVIDENCE_ROTATION_KEY_VERSION_INVALID:{error}"))?;
            let stored_bytes = row
                .get::<_, i64>(2)
                .map_err(|error| format!("EVIDENCE_ROTATION_STORED_BYTES_INVALID:{error}"))?;
            rows.push((
                digest,
                u32::try_from(key_version)
                    .map_err(|_| "EVIDENCE_ROTATION_KEY_VERSION_INVALID".to_owned())?,
                u64::try_from(stored_bytes)
                    .map_err(|_| "EVIDENCE_ROTATION_STORED_BYTES_INVALID".to_owned())?,
            ));
        }
        Ok(rows)
    }

    fn update_rotation_index(
        &self,
        digest: &str,
        stored_bytes: u64,
        key_version: u32,
        require_row: bool,
    ) -> Result<(), String> {
        let mut connection = Connection::open(self.root.join("evidence.sqlite3"))
            .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
        connection
            .execute_batch(
                "PRAGMA foreign_keys=ON; PRAGMA busy_timeout=30000; PRAGMA synchronous=FULL;",
            )
            .map_err(|error| format!("EVIDENCE_INDEX_PRAGMA_FAILED:{error}"))?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| format!("EVIDENCE_INDEX_TRANSACTION_FAILED:{error}"))?;
        let changed = transaction
            .execute(
                "UPDATE evidence_objects SET stored_bytes=?1,key_version=?2 WHERE digest=?3",
                params![
                    i64::try_from(stored_bytes)
                        .map_err(|_| "EVIDENCE_STORED_SIZE_INVALID".to_owned())?,
                    i64::from(key_version),
                    digest,
                ],
            )
            .map_err(|error| format!("EVIDENCE_ROTATION_INDEX_UPDATE_FAILED:{error}"))?;
        if require_row && changed != 1 {
            return Err(format!("EVIDENCE_ROTATION_INDEX_OBJECT_MISSING:{digest}"));
        }
        transaction
            .commit()
            .map_err(|error| format!("EVIDENCE_INDEX_COMMIT_FAILED:{error}"))
    }

    fn restore_rotation_state(
        &self,
        digest: &str,
        object_path: &Path,
        backup_path: &Path,
        metadata_path: &Path,
        original_metadata: &Value,
        original_stored_size: u64,
        source_version: u32,
    ) -> Result<(), String> {
        if object_path.exists() {
            fs::remove_file(object_path)
                .map_err(|error| format!("EVIDENCE_ROTATION_ROLLBACK_REMOVE_FAILED:{error}"))?;
        }
        fs::rename(backup_path, object_path)
            .map_err(|error| format!("EVIDENCE_ROTATION_ROLLBACK_RESTORE_FAILED:{error}"))?;
        atomic_write_json(metadata_path, original_metadata)?;
        self.update_rotation_index(digest, original_stored_size, source_version, false)
    }

    fn reencrypt_object(&self, digest: &str, target_version: u32) -> Result<(bool, u64), String> {
        let object_path = self.object_path(digest);
        let metadata_path = self.metadata_path(digest);
        if !object_path.is_file() || !metadata_path.is_file() {
            return Err(format!("EVIDENCE_ROTATION_OBJECT_INCOMPLETE:{digest}"));
        }
        let original_metadata = serde_json::from_slice::<Value>(
            &fs::read(&metadata_path)
                .map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_METADATA_INVALID:{error}"))?;
        let source_raw = original_metadata
            .get("encryption")
            .and_then(|value| value.get("key_version"))
            .ok_or_else(|| format!("EVIDENCE_ROTATION_METADATA_INVALID:{digest}"))?;
        let source_version = u32::try_from(Self::rotation_integer(
            source_raw,
            "encryption.key_version",
        )?)
        .map_err(|_| "EVIDENCE_ROTATION_KEY_VERSION_INVALID".to_owned())?;
        if source_version == target_version {
            return fs::metadata(&object_path)
                .map(|metadata| (false, metadata.len()))
                .map_err(|error| format!("EVIDENCE_OBJECT_METADATA_FAILED:{error}"));
        }
        let name = object_path
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| "EVIDENCE_ROTATION_OBJECT_NAME_INVALID".to_owned())?;
        let staging_path = object_path.with_file_name(format!(".{name}.rotate-{target_version}"));
        let backup_path = object_path.with_file_name(format!(".{name}.rotate-backup"));
        let original_stored_size = fs::metadata(&object_path)
            .map_err(|error| format!("EVIDENCE_OBJECT_METADATA_FAILED:{error}"))?
            .len();
        let _ = fs::remove_file(&staging_path);
        let _ = fs::remove_file(&backup_path);
        let mut plaintext = self.decrypt_digest(digest)?;
        let mut backed_up = false;
        let operation = (|| -> Result<(bool, u64), String> {
            let stored_size =
                self.encrypt_object(&plaintext, &staging_path, digest, target_version)?;
            fs::rename(&object_path, &backup_path)
                .map_err(|error| format!("EVIDENCE_ROTATION_BACKUP_FAILED:{error}"))?;
            backed_up = true;
            fs::rename(&staging_path, &object_path)
                .map_err(|error| format!("EVIDENCE_ROTATION_REPLACE_FAILED:{error}"))?;
            let mut updated_metadata = original_metadata.clone();
            updated_metadata["stored_bytes"] = Value::from(stored_size);
            let encryption = updated_metadata
                .get_mut("encryption")
                .and_then(Value::as_object_mut)
                .ok_or_else(|| format!("EVIDENCE_ROTATION_METADATA_INVALID:{digest}"))?;
            encryption.insert(
                "algorithm".to_owned(),
                Value::String("AES-256-GCM".to_owned()),
            );
            encryption.insert("key_version".to_owned(), Value::from(target_version));
            encryption.insert("mode".to_owned(), Value::String("encrypted".to_owned()));
            atomic_write_json(&metadata_path, &updated_metadata)?;
            self.update_rotation_index(digest, stored_size, target_version, true)?;
            fs::remove_file(&backup_path)
                .map_err(|error| format!("EVIDENCE_ROTATION_BACKUP_DELETE_FAILED:{error}"))?;
            backed_up = false;
            Ok((true, stored_size))
        })();
        plaintext.zeroize();
        let _ = fs::remove_file(&staging_path);
        match operation {
            Ok(value) => Ok(value),
            Err(error) => {
                if backed_up && backup_path.exists() {
                    if let Err(rollback_error) = self.restore_rotation_state(
                        digest,
                        &object_path,
                        &backup_path,
                        &metadata_path,
                        &original_metadata,
                        original_stored_size,
                        source_version,
                    ) {
                        return Err(format!(
                            "{error};EVIDENCE_ROTATION_ROLLBACK_FAILED:{rollback_error}"
                        ));
                    }
                }
                let _ = fs::remove_file(&backup_path);
                Err(error)
            }
        }
    }

    fn rotate_key(&mut self) -> Result<Value, String> {
        let (previous_version, active_version) = self.rotate_local_key()?;
        let rows = self.rotation_rows()?;
        let objects = rows.len();
        let mut reencrypted = 0_usize;
        let mut skipped = 0_usize;
        let mut stored_bytes = 0_u64;
        for (digest, _, _) in &rows {
            let (changed, size) = self.reencrypt_object(digest, active_version)?;
            stored_bytes = stored_bytes
                .checked_add(size)
                .ok_or_else(|| "EVIDENCE_ROTATION_STORED_BYTES_OVERFLOW".to_owned())?;
            if changed {
                reencrypted += 1;
            } else {
                skipped += 1;
            }
        }
        Ok(json!({
            "ok": true,
            "previous_key_version": previous_version,
            "active_key_version": active_version,
            "reencrypt": true,
            "objects": objects,
            "reencrypted": reencrypted,
            "skipped": skipped,
            "stored_bytes": stored_bytes,
        }))
    }
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    if is_rotate_key(arguments) {
        return RotationStore::open(state_root, &project_id)?.rotate_key();
    }
    let parsed = parse_arguments(arguments)?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let data = evidence.get_with_max_bytes(&parsed.handle, parsed.max_bytes)?;
    if let Some(output) = parsed.output.filter(|value| !value.is_empty()) {
        write_output(&PathBuf::from(&output), &data)?;
        return Ok(json!({
            "handle": parsed.handle,
            "bytes": data.len(),
            "output": output,
        }));
    }
    Ok(json!({
        "handle": parsed.handle,
        "bytes": data.len(),
        "text": String::from_utf8_lossy(&data),
    }))
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, supports, GetArguments};

    #[test]
    fn routes_evidence_get_and_rotate_key_only() {
        assert!(supports(&["evidence".to_owned(), "get".to_owned()]));
        assert!(supports(&["evidence".to_owned(), "rotate-key".to_owned()]));
        assert!(!supports(&["evidence".to_owned(), "describe".to_owned()]));
    }

    #[test]
    fn repeated_options_use_python_last_value_semantics() {
        let parsed = parse_arguments(&[
            "evidence".to_owned(),
            "get".to_owned(),
            "--max-bytes".to_owned(),
            "1".to_owned(),
            "sc://sha256/abc".to_owned(),
            "--max-bytes=9".to_owned(),
            "--output=first.bin".to_owned(),
            "--output".to_owned(),
            "second.bin".to_owned(),
        ])
        .expect("parse");
        assert_eq!(
            parsed,
            GetArguments {
                handle: "sc://sha256/abc".to_owned(),
                max_bytes: Some(9),
                output: Some("second.bin".to_owned()),
            }
        );
    }

    #[test]
    fn accepts_negative_limit_for_runtime_parity() {
        let parsed = parse_arguments(&[
            "evidence".to_owned(),
            "get".to_owned(),
            "sc://sha256/abc".to_owned(),
            "--max-bytes=-1".to_owned(),
        ])
        .expect("parse");
        assert_eq!(parsed.max_bytes, Some(-1));
    }
}
