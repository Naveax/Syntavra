#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use aes_gcm::aead::{AeadInPlace as _, KeyInit as _};
use aes_gcm::{Aes256Gcm, Nonce, Tag};
use base64::Engine as _;
use hkdf::Hkdf;
use rand::{rngs::OsRng, RngCore as _};
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};
use zeroize::Zeroize;

use super::native_backup::{initialize_evidence_state, set_private};

const MAGIC: &[u8; 6] = b"SCEV1\0";
const NONCE_BYTES: usize = 12;
const TAG_BYTES: usize = 16;
const KEY_BYTES: usize = 32;
const SCHEMA_VERSION: u64 = 3;
const MANAGED_KEY_ENV: &str = "SYNTAVRA_EVIDENCE_KEY";

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

fn decode_managed_key(value: &str) -> Result<[u8; KEY_BYTES], String> {
    let raw = value.trim();
    let bytes = if raw.len() == KEY_BYTES * 2 && raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        let mut output = [0_u8; KEY_BYTES];
        for (index, slot) in output.iter_mut().enumerate() {
            let offset = index * 2;
            *slot = u8::from_str_radix(&raw[offset..offset + 2], 16)
                .map_err(|error| format!("EVIDENCE_KEY_HEX_INVALID:{error}"))?;
        }
        output.to_vec()
    } else {
        let mut padded = raw.to_owned();
        while padded.len() % 4 != 0 {
            padded.push('=');
        }
        base64::engine::general_purpose::URL_SAFE
            .decode(padded)
            .map_err(|error| format!("EVIDENCE_KEY_BASE64_INVALID:{error}"))?
    };
    bytes
        .try_into()
        .map_err(|_| "EVIDENCE_KEY_LENGTH_INVALID".to_owned())
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("EVIDENCE_JSON_SERIALIZE_FAILED:{error}"))
}

fn atomic_write(path: &Path, payload: &[u8], private: bool) -> Result<(), String> {
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
    if private {
        set_private(path);
    }
    Ok(())
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    let mut payload = canonical_json(value)?;
    payload.push(b'\n');
    atomic_write(path, &payload, true)
}

fn parse_handle(handle: &str) -> Result<String, String> {
    let prefix = "sc://sha256/";
    let digest = handle
        .strip_prefix(prefix)
        .ok_or_else(|| "EVIDENCE_HANDLE_INVALID".to_owned())?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("EVIDENCE_DIGEST_INVALID".to_owned());
    }
    Ok(digest.to_owned())
}

pub(crate) struct NativeEvidenceStore {
    root: PathBuf,
    project_id: String,
    active_version: u32,
    managed_key: Option<[u8; KEY_BYTES]>,
}

impl NativeEvidenceStore {
    pub(crate) fn open(state_root: &Path, project_id: &str) -> Result<Self, String> {
        initialize_evidence_state(state_root)?;
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
        let managed_key = match env::var(MANAGED_KEY_ENV) {
            Ok(value) if !value.trim().is_empty() => Some(decode_managed_key(&value)?),
            _ => None,
        };
        Ok(Self {
            root,
            project_id: project_id.to_owned(),
            active_version,
            managed_key,
        })
    }

    fn object_path(&self, digest: &str) -> PathBuf {
        self.root.join("objects").join(&digest[..2]).join(&digest[2..])
    }

    fn metadata_path(&self, digest: &str) -> PathBuf {
        self.root.join("metadata").join(format!("{digest}.json"))
    }

    fn master_key(&self, version: u32) -> Result<[u8; KEY_BYTES], String> {
        if version == 0 {
            return Err("EVIDENCE_KEY_VERSION_INVALID".to_owned());
        }
        if let Some(key) = self.managed_key {
            if version != 1 {
                return Err("EVIDENCE_MANAGED_HISTORICAL_KEY_UNAVAILABLE".to_owned());
            }
            return Ok(key);
        }
        let path = self.root.join("keys").join(format!("master-v{version}.key"));
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
        canonical_json(&serde_json::to_value(value).map_err(|error| {
            format!("EVIDENCE_AAD_VALUE_FAILED:{error}")
        })?)
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

        let mut payload = Vec::with_capacity(
            MAGIC.len() + 4 + NONCE_BYTES + ciphertext.len() + TAG_BYTES,
        );
        payload.extend_from_slice(MAGIC);
        payload.extend_from_slice(&version.to_be_bytes());
        payload.extend_from_slice(&nonce_bytes);
        payload.extend_from_slice(&ciphertext);
        payload.extend_from_slice(tag.as_ref());
        atomic_write(destination, &payload, true)?;
        u64::try_from(payload.len()).map_err(|_| "EVIDENCE_STORED_SIZE_INVALID".to_owned())
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

    fn update_metadata(
        &self,
        digest: &str,
        plaintext_bytes: u64,
        stored_bytes: u64,
        kind: &str,
        metadata: &Value,
        version: u32,
    ) -> Result<(), String> {
        let path = self.metadata_path(digest);
        if path.is_file() {
            let mut value = serde_json::from_slice::<Value>(
                &fs::read(&path)
                    .map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
            )
            .map_err(|error| format!("EVIDENCE_METADATA_INVALID:{error}"))?;
            if value["project_id"].as_str() != Some(self.project_id.as_str()) {
                return Err("EVIDENCE_SCOPE_MISMATCH".to_owned());
            }
            if value["schema_version"].as_u64() != Some(SCHEMA_VERSION) {
                return Err("EVIDENCE_METADATA_SCHEMA_INVALID".to_owned());
            }
            let provenance = value["provenance"]
                .as_array_mut()
                .ok_or_else(|| "EVIDENCE_PROVENANCE_INVALID".to_owned())?;
            if !provenance.iter().any(|candidate| candidate == metadata) {
                provenance.push(metadata.clone());
                if provenance.len() > 128 {
                    let remove = provenance.len() - 128;
                    provenance.drain(..remove);
                }
            }
            atomic_write_json(&path, &value)?;
            return Ok(());
        }
        atomic_write_json(
            &path,
            &json!({
                "schema_version": SCHEMA_VERSION,
                "digest": digest,
                "bytes": plaintext_bytes,
                "stored_bytes": stored_bytes,
                "project_id": self.project_id,
                "kind": kind,
                "created_at": now_seconds()?,
                "expires_at": Value::Null,
                "encryption": {
                    "algorithm": "AES-256-GCM",
                    "key_version": version,
                    "mode": "encrypted",
                },
                "provenance": [metadata.clone()],
            }),
        )
    }

    fn update_index(
        &self,
        digest: &str,
        plaintext_bytes: u64,
        stored_bytes: u64,
        version: u32,
    ) -> Result<(), String> {
        let now = now_seconds()?;
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
        transaction
            .execute(
                r#"
                INSERT INTO evidence_objects(
                    digest,plaintext_bytes,stored_bytes,key_version,created_at,
                    last_accessed_at,expires_at,ref_count
                ) VALUES(?1,?2,?3,?4,?5,?5,NULL,0)
                ON CONFLICT(digest) DO UPDATE SET
                    last_accessed_at=excluded.last_accessed_at,
                    expires_at=CASE
                        WHEN evidence_objects.expires_at IS NULL THEN excluded.expires_at
                        WHEN excluded.expires_at IS NULL THEN evidence_objects.expires_at
                        ELSE MAX(evidence_objects.expires_at, excluded.expires_at)
                    END
                "#,
                params![
                    digest,
                    i64::try_from(plaintext_bytes)
                        .map_err(|_| "EVIDENCE_PLAINTEXT_SIZE_INVALID".to_owned())?,
                    i64::try_from(stored_bytes)
                        .map_err(|_| "EVIDENCE_STORED_SIZE_INVALID".to_owned())?,
                    i64::from(version),
                    now,
                ],
            )
            .map_err(|error| format!("EVIDENCE_INDEX_UPSERT_FAILED:{error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("EVIDENCE_INDEX_COMMIT_FAILED:{error}"))?;
        set_private(&self.root.join("evidence.sqlite3"));
        Ok(())
    }

    pub(crate) fn put(
        &self,
        data: &[u8],
        kind: &str,
        metadata: &Value,
    ) -> Result<String, String> {
        let digest = hex(&Sha256::digest(data));
        let path = self.object_path(&digest);
        let stored_bytes = if path.is_file() {
            let mut verified = self.decrypt_digest(&digest)?;
            verified.zeroize();
            fs::metadata(&path)
                .map_err(|error| format!("EVIDENCE_OBJECT_METADATA_FAILED:{error}"))?
                .len()
        } else {
            self.encrypt_object(data, &path, &digest, self.active_version)?
        };
        let plaintext_bytes =
            u64::try_from(data.len()).map_err(|_| "EVIDENCE_PLAINTEXT_SIZE_INVALID".to_owned())?;
        self.update_metadata(
            &digest,
            plaintext_bytes,
            stored_bytes,
            kind,
            metadata,
            self.active_version,
        )?;
        self.update_index(
            &digest,
            plaintext_bytes,
            stored_bytes,
            self.active_version,
        )?;
        Ok(format!("sc://sha256/{digest}"))
    }

    pub(crate) fn get(&self, handle: &str) -> Result<Vec<u8>, String> {
        let digest = parse_handle(handle)?;
        let metadata = serde_json::from_slice::<Value>(
            &fs::read(self.metadata_path(&digest))
                .map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_METADATA_INVALID:{error}"))?;
        if metadata["project_id"].as_str() != Some(self.project_id.as_str()) {
            return Err("EVIDENCE_SCOPE_MISMATCH".to_owned());
        }
        let output = self.decrypt_digest(&digest)?;
        let connection = Connection::open(self.root.join("evidence.sqlite3"))
            .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
        connection
            .execute(
                "UPDATE evidence_objects SET last_accessed_at=?1 WHERE digest=?2",
                params![now_seconds()?, digest],
            )
            .map_err(|error| format!("EVIDENCE_ACCESS_UPDATE_FAILED:{error}"))?;
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_handle, MAGIC};

    #[test]
    fn evidence_header_matches_python_contract() {
        assert_eq!(MAGIC, b"SCEV1\0");
    }

    #[test]
    fn handle_requires_lowercase_sha256() {
        assert!(parse_handle(&format!("sc://sha256/{}", "a".repeat(64))).is_ok());
        assert!(parse_handle(&format!("sc://sha256/{}", "A".repeat(64))).is_err());
    }
}
