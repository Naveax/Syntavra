#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
EVIDENCE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_store.rs"
ROTATE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_rotate_key.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_evidence_rotate_key.rs"]
mod native_evidence_rotate_key;
'''
MODULE_ANCHOR = '''#[path = "native_evidence_get.rs"]
mod native_evidence_get;
'''
SUPPORT = "        || native_evidence_rotate_key::supports(command)\n"
SUPPORT_ANCHOR = "        || native_evidence_get::supports(command)\n"
EXECUTE = '''    if native_evidence_rotate_key::supports(command) {
        return native_evidence_rotate_key::execute(project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_evidence_get::supports(command) {
        return native_evidence_get::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_evidence_rotate_key_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_evidence_get_r38.py",\n'
STORE_ANCHOR = '''    pub(crate) fn put(&self, data: &[u8], kind: &str, metadata: &Value) -> Result<String, String> {
'''

ROTATION_METHODS = r'''    fn rotation_integer(value: &Value, field: &str) -> Result<i128, String> {
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
        if self.managed_key.is_some() {
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
            let result = atomic_write(&key_path, &key, true);
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
            .prepare(
                "SELECT digest,key_version,stored_bytes FROM evidence_objects ORDER BY digest",
            )
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
        self.update_rotation_index(
            digest,
            original_stored_size,
            source_version,
            false,
        )
    }

    fn reencrypt_object(
        &self,
        digest: &str,
        target_version: u32,
    ) -> Result<(bool, u64), String> {
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

    pub(crate) fn rotate_key(&mut self, reencrypt: bool) -> Result<Value, String> {
        let (previous_version, active_version) = self.rotate_local_key()?;
        let rows = self.rotation_rows()?;
        let objects = rows.len();
        let mut reencrypted = 0_usize;
        let mut skipped = 0_usize;
        let mut stored_bytes = 0_u64;
        if reencrypt {
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
        } else {
            for (_, _, size) in &rows {
                stored_bytes = stored_bytes
                    .checked_add(*size)
                    .ok_or_else(|| "EVIDENCE_ROTATION_STORED_BYTES_OVERFLOW".to_owned())?;
            }
            skipped = objects;
        }
        Ok(json!({
            "ok": true,
            "previous_key_version": previous_version,
            "active_key_version": active_version,
            "reencrypt": reencrypt,
            "objects": objects,
            "reencrypted": reencrypted,
            "skipped": skipped,
            "stored_bytes": stored_bytes,
        }))
    }

'''


def validate_sources() -> None:
    for path in (ROTATE, EVIDENCE):
        if not path.is_file():
            raise RuntimeError(f"evidence rotation dependency is missing: {path}")
    source = EVIDENCE.read_text(encoding="utf-8")
    for marker in ("fn encrypt_object", "fn decrypt_digest", "pub(crate) fn get"):
        if marker not in source:
            raise RuntimeError(f"evidence rotation dependency is missing: {marker}")


def repair_store() -> bool:
    source = EVIDENCE.read_text(encoding="utf-8")
    if "pub(crate) fn rotate_key" in source:
        return False
    if source.count(STORE_ANCHOR) != 1:
        raise RuntimeError("evidence rotation store anchor is ambiguous")
    EVIDENCE.write_text(
        source.replace(STORE_ANCHOR, ROTATION_METHODS + STORE_ANCHOR, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if "mod native_evidence_rotate_key;" not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("evidence rotation module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + MODULE, 1)
        changed = True
    if "|| native_evidence_rotate_key::supports(command)" not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("evidence rotation support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True
    support = "if native_evidence_rotate_key::supports(command) {"
    call = "native_evidence_rotate_key::execute("
    presence = (support in rendered, call in rendered)
    if presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("evidence rotation execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif presence != (True, True):
        raise RuntimeError("evidence rotation execute wiring is partial")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("evidence rotation validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    validate_sources()
    store_changed = repair_store()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": store_changed or product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "store_changed": store_changed,
                "surface": "native-evidence-rotate-key",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
