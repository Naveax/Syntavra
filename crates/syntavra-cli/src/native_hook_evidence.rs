#![forbid(unsafe_code)]
#![allow(clippy::pedantic, clippy::too_many_lines)]

use std::collections::BTreeMap;
use std::fs;
use std::io::Write as _;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use aes_gcm::aead::{Aead, Payload};
use aes_gcm::{Aes256Gcm, KeyInit, Nonce};
use hkdf::Hkdf;
use rand::rngs::OsRng;
use rand::RngCore as _;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use sha2::Sha256;
use syntavra_core::sha256_hex;

const MAGIC: &[u8] = b"SCEV1\0";

pub(super) fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|_| "HOOK_OUTPUT_SYSTEM_TIME_INVALID".to_owned())
}

fn canonical_into(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(
            &serde_json::to_string(value)
                .map_err(|error| format!("HOOK_OUTPUT_JSON_STRING_FAILED:{error}"))?,
        ),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                canonical_into(value, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            for (index, (key, value)) in ordered.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(
                    &serde_json::to_string(key)
                        .map_err(|error| format!("HOOK_OUTPUT_JSON_KEY_FAILED:{error}"))?,
                );
                output.push(':');
                canonical_into(value, output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

pub(super) fn canonical(value: &Value) -> Result<String, String> {
    let mut output = String::new();
    canonical_into(value, &mut output)?;
    Ok(output)
}

pub(super) fn hash_json(value: &Value) -> Result<String, String> {
    Ok(sha256_hex(canonical(value)?.as_bytes()))
}

pub(super) fn policy() -> Value {
    json!({
        "profile": "balanced",
        "preview_budget_bytes": 4096,
        "passthrough_threshold_bytes": 768,
        "segment_target_bytes": 16384,
        "reveal_page_bytes": 8192,
        "min_externalization_ratio": 0.1,
        "max_critical_segments": 32,
        "delta_enabled": true,
        "deduplicate": true,
        "continuation_ttl_seconds": 900,
        "search_window_lines": 2,
    })
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "HOOK_OUTPUT_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent).map_err(|error| format!("HOOK_OUTPUT_DIR_FAILED:{error}"))?;
    let mut random = [0_u8; 8];
    OsRng.fill_bytes(&mut random);
    let temp = parent.join(format!(".syntavra-{:016x}.tmp", u64::from_be_bytes(random)));
    let mut file =
        fs::File::create(&temp).map_err(|error| format!("HOOK_OUTPUT_TEMP_FAILED:{error}"))?;
    file.write_all(bytes)
        .map_err(|error| format!("HOOK_OUTPUT_WRITE_FAILED:{error}"))?;
    file.sync_all()
        .map_err(|error| format!("HOOK_OUTPUT_SYNC_FAILED:{error}"))?;
    fs::rename(temp, path).map_err(|error| format!("HOOK_OUTPUT_RENAME_FAILED:{error}"))
}

#[cfg(unix)]
fn private(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt as _;

    let mut permissions = fs::metadata(path)
        .map_err(|error| format!("HOOK_OUTPUT_METADATA_FAILED:{error}"))?
        .permissions();
    permissions.set_mode(0o600);
    fs::set_permissions(path, permissions)
        .map_err(|error| format!("HOOK_OUTPUT_PERMISSIONS_FAILED:{error}"))
}

#[cfg(not(unix))]
fn private(_path: &Path) -> Result<(), String> {
    Ok(())
}

fn evidence_schema(path: &Path) -> Result<Connection, String> {
    let db =
        Connection::open(path).map_err(|error| format!("HOOK_EVIDENCE_DB_OPEN_FAILED:{error}"))?;
    db.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         PRAGMA busy_timeout=30000;
         PRAGMA synchronous=FULL;
         CREATE TABLE IF NOT EXISTS evidence_objects(
            digest TEXT PRIMARY KEY,
            plaintext_bytes INTEGER NOT NULL,
            stored_bytes INTEGER NOT NULL,
            key_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            last_accessed_at REAL NOT NULL,
            expires_at REAL,
            ref_count INTEGER NOT NULL DEFAULT 0,
            legal_hold INTEGER NOT NULL DEFAULT 0);
         CREATE TABLE IF NOT EXISTS evidence_references(
            digest TEXT NOT NULL,
            reference TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(digest,reference),
            FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE);
         CREATE INDEX IF NOT EXISTS evidence_expiry_idx ON evidence_objects(expires_at);",
    )
    .map_err(|error| format!("HOOK_EVIDENCE_SCHEMA_FAILED:{error}"))?;
    Ok(db)
}

pub(super) fn externalization_schema(path: &Path) -> Result<Connection, String> {
    let db = Connection::open(path).map_err(|error| format!("HOOK_EXT_DB_OPEN_FAILED:{error}"))?;
    db.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA foreign_keys=ON;
         PRAGMA busy_timeout=30000;
         PRAGMA synchronous=FULL;
         CREATE TABLE IF NOT EXISTS ext_artifacts(
            artifact_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL,
            stream_key TEXT NOT NULL,
            identity_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            family TEXT NOT NULL,
            mode TEXT NOT NULL,
            preview TEXT NOT NULL,
            original_bytes INTEGER NOT NULL,
            visible_bytes INTEGER NOT NULL,
            exact_handle TEXT NOT NULL,
            segment_count INTEGER NOT NULL,
            merkle_root TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            quality_gate_passed INTEGER NOT NULL,
            baseline_artifact_id TEXT,
            injection_risk INTEGER NOT NULL,
            facets_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at REAL NOT NULL);
         CREATE INDEX IF NOT EXISTS ext_artifacts_scope_stream
            ON ext_artifacts(scope_key,stream_key,created_at DESC);
         CREATE TABLE IF NOT EXISTS ext_segments(
            artifact_id TEXT NOT NULL,
            segment_index INTEGER NOT NULL,
            start_byte INTEGER NOT NULL,
            end_byte INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            exact_handle TEXT NOT NULL,
            kind TEXT NOT NULL,
            salience REAL NOT NULL,
            critical INTEGER NOT NULL,
            index_text TEXT NOT NULL,
            PRIMARY KEY(artifact_id,segment_index),
            FOREIGN KEY(artifact_id) REFERENCES ext_artifacts(artifact_id) ON DELETE CASCADE);
         CREATE TABLE IF NOT EXISTS ext_seen(
            scope_key TEXT NOT NULL,
            identity_key TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            seen_count INTEGER NOT NULL,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            PRIMARY KEY(scope_key,identity_key));
         CREATE TABLE IF NOT EXISTS ext_continuations(
            token_hash TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            lens TEXT NOT NULL,
            query TEXT NOT NULL,
            segment_indexes_json TEXT NOT NULL,
            segment_position INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            expires_at REAL NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0);",
    )
    .map_err(|error| format!("HOOK_EXT_SCHEMA_FAILED:{error}"))?;
    Ok(db)
}

pub(super) fn evidence_put(
    state_root: &Path,
    project_root: &Path,
    raw: &[u8],
    kind: &str,
    provenance: Value,
) -> Result<String, String> {
    let digest = sha256_hex(raw);
    let root = state_root.join("evidence");
    let keys = root.join("keys");
    fs::create_dir_all(&keys).map_err(|error| format!("HOOK_EVIDENCE_KEYS_FAILED:{error}"))?;

    let active = keys.join("active.json");
    if !active.exists() {
        atomic_write(
            &active,
            b"{\n  \"active_version\": 1,\n  \"schema_version\": 1\n}\n",
        )?;
        private(&active)?;
    }

    let key_path = keys.join("master-v1.key");
    if !key_path.exists() {
        let mut key = [0_u8; 32];
        OsRng.fill_bytes(&mut key);
        atomic_write(&key_path, &key)?;
        private(&key_path)?;
    }
    let master =
        fs::read(&key_path).map_err(|error| format!("HOOK_EVIDENCE_KEY_READ_FAILED:{error}"))?;
    if master.len() != 32 {
        return Err("HOOK_EVIDENCE_KEY_INVALID".to_owned());
    }

    let project_id = super::super::super::state_snapshot_contract::project_id_for_root(
        &project_root.to_string_lossy(),
    )?;
    let salt = syntavra_core::sha256(format!("syntavra:evidence:{project_id}").as_bytes());
    let hkdf = Hkdf::<Sha256>::new(Some(&salt), &master);
    let mut key = [0_u8; 32];
    hkdf.expand(b"syntavra-evidence-v1", &mut key)
        .map_err(|_| "HOOK_EVIDENCE_HKDF_FAILED".to_owned())?;

    let object = root.join("objects").join(&digest[..2]).join(&digest[2..]);
    let created = now()?;
    let stored_bytes = if object.exists() {
        fs::metadata(&object)
            .map_err(|error| format!("HOOK_EVIDENCE_OBJECT_METADATA_FAILED:{error}"))?
            .len() as usize
    } else {
        let mut nonce = [0_u8; 12];
        OsRng.fill_bytes(&mut nonce);
        let aad = canonical(&json!({
            "schema": 3,
            "project_id": project_id.clone(),
            "digest": digest.clone(),
            "key_version": 1,
        }))?;
        let cipher = Aes256Gcm::new_from_slice(&key)
            .map_err(|_| "HOOK_EVIDENCE_CIPHER_FAILED".to_owned())?;
        let encrypted = cipher
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: raw,
                    aad: aad.as_bytes(),
                },
            )
            .map_err(|_| "HOOK_EVIDENCE_ENCRYPT_FAILED".to_owned())?;
        let mut stored = Vec::with_capacity(MAGIC.len() + 4 + nonce.len() + encrypted.len());
        stored.extend_from_slice(MAGIC);
        stored.extend_from_slice(&1_u32.to_be_bytes());
        stored.extend_from_slice(&nonce);
        stored.extend_from_slice(&encrypted);
        atomic_write(&object, &stored)?;
        private(&object)?;
        stored.len()
    };

    let metadata = root.join("metadata").join(format!("{digest}.json"));
    if !metadata.exists() {
        let value = json!({
            "schema_version": 3,
            "digest": digest,
            "bytes": raw.len(),
            "stored_bytes": stored_bytes,
            "project_id": project_id,
            "kind": kind,
            "created_at": created,
            "expires_at": Value::Null,
            "encryption": {
                "algorithm": "AES-256-GCM",
                "key_version": 1,
                "mode": "encrypted"
            },
            "provenance": [provenance],
        });
        atomic_write(
            &metadata,
            format!(
                "{}\n",
                serde_json::to_string_pretty(&value)
                    .map_err(|error| format!("HOOK_EVIDENCE_METADATA_JSON_FAILED:{error}"))?
            )
            .as_bytes(),
        )?;
        private(&metadata)?;
    }

    let db = evidence_schema(&root.join("evidence.sqlite3"))?;
    db.execute(
        "INSERT INTO evidence_objects(
            digest,plaintext_bytes,stored_bytes,key_version,created_at,last_accessed_at,expires_at,ref_count)
         VALUES(?1,?2,?3,1,?4,?4,NULL,0)
         ON CONFLICT(digest) DO UPDATE SET last_accessed_at=excluded.last_accessed_at",
        params![digest, raw.len() as i64, stored_bytes as i64, created],
    )
    .map_err(|error| format!("HOOK_EVIDENCE_UPSERT_FAILED:{error}"))?;

    Ok(format!("sc://sha256/{digest}"))
}
