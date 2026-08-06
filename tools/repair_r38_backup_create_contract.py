#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARGO = ROOT / "crates" / "syntavra-cli" / "Cargo.toml"
BACKUP = ROOT / "crates" / "syntavra-cli" / "src" / "native_backup.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

CHACHA_DEPENDENCY = 'chacha20poly1305 = "0.10.1"\n'
CHACHA_ANCHOR = 'base64 = "0.22.1"\n'
TAR_DEPENDENCY = 'tar = "0.4.44"\n'
TAR_ANCHOR = 'syntavra-core = { path = "../syntavra-core" }\n'

UNUSED_HASH = '''fn sha256_bytes(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

'''

OLD_SQLITE_BACKUP = '''        let backup = Backup::new(&source_connection, &mut target_connection)?;
        backup.run_to_completion(5, Duration::from_millis(0), None)?;
        let integrity = target_connection.query_row("PRAGMA integrity_check", [], |row| {
'''
NEW_SQLITE_BACKUP = '''        let backup = Backup::new(&source_connection, &mut target_connection)?;
        backup.run_to_completion(5, Duration::from_millis(0), None)?;
        drop(backup);
        let integrity = target_connection.query_row("PRAGMA integrity_check", [], |row| {
'''

OLD_REGISTRY = '''        let registry = json!({
            "schema_version": 1,
            "active": key_id,
            "keys": [key_id],
        });
'''
NEW_REGISTRY = '''        let registry = json!({
            "schema_version": 1,
            "active": key_id.clone(),
            "keys": [key_id.clone()],
        });
'''

OLD_CHUNK_READ = '''    for index in 0..count {
        let mut plaintext = vec![0_u8; DEFAULT_CHUNK_BYTES];
        let read = input
            .read(&mut plaintext)
            .map_err(|error| format!("BACKUP_SEAL_READ_FAILED:{error}"))?;
        plaintext.truncate(read);
        let mut nonce = [0_u8; 24];
'''
NEW_CHUNK_READ = '''    for index in 0..count {
        let offset = index * DEFAULT_CHUNK_BYTES as u64;
        let read = usize::try_from((size - offset).min(DEFAULT_CHUNK_BYTES as u64))
            .map_err(|_| "BACKUP_SEAL_CHUNK_LENGTH_INVALID".to_owned())?;
        let mut plaintext = vec![0_u8; read];
        input
            .read_exact(&mut plaintext)
            .map_err(|error| format!("BACKUP_SEAL_READ_FAILED:{error}"))?;
        let mut nonce = [0_u8; 24];
'''

OLD_TAG_WRITE = '''            .and_then(|_| output.write_all(tag.as_slice()))
'''
NEW_TAG_WRITE = '''            .and_then(|_| output.write_all(tag.as_ref()))
'''

OLD_REPLACE = '''            seal_file(&archive, &temporary_destination, &project_id, &active)?;
            fs::rename(&temporary_destination, &destination)
                .map_err(|error| format!("BACKUP_DESTINATION_REPLACE_FAILED:{error}"))?;
'''
NEW_REPLACE = '''            seal_file(&archive, &temporary_destination, &project_id, &active)?;
            if destination.is_file() {
                fs::remove_file(&destination)
                    .map_err(|error| format!("BACKUP_DESTINATION_REMOVE_FAILED:{error}"))?;
            }
            fs::rename(&temporary_destination, &destination)
                .map_err(|error| format!("BACKUP_DESTINATION_REPLACE_FAILED:{error}"))?;
'''

MODULE = '''#[path = "native_backup.rs"]
mod native_backup;
'''
MODULE_ANCHOR = '''#[path = "native_cache_amortize.rs"]
mod native_cache_amortize;
'''
SUPPORT = "        || native_backup::supports(command)\n"
SUPPORT_ANCHOR = "        || native_config_read_only::supports(command)\n"
EXECUTE = '''    if native_backup::supports(command) {
        return native_backup::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_platform_health::supports(command) {
        let value = native_platform_health::execute(command, project_root, state_root)?;
'''
TARGET = '    "tests/runtime/test_native_backup_create_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_platform_health_r38.py",\n'


def insert_after(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, anchor + token, 1), True


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if source.count(new) == 1:
        return source, False
    if source.count(new) != 0:
        raise RuntimeError(f"{label} repaired count invalid")
    if source.count(old) != 1:
        raise RuntimeError(f"{label} legacy contract must be unique")
    return source.replace(old, new, 1), True


def repair_dependencies() -> bool:
    source = CARGO.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (CHACHA_DEPENDENCY, CHACHA_ANCHOR, "XChaCha dependency"),
        (TAR_DEPENDENCY, TAR_ANCHOR, "tar dependency"),
    ):
        rendered, applied = insert_after(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        CARGO.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_backup_source() -> bool:
    source = BACKUP.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if UNUSED_HASH in rendered:
        if rendered.count(UNUSED_HASH) != 1:
            raise RuntimeError("unused backup hash helper must be unique")
        rendered = rendered.replace(UNUSED_HASH, "", 1)
        changed = True
    for old, new, label in (
        (OLD_SQLITE_BACKUP, NEW_SQLITE_BACKUP, "SQLite backup lifetime"),
        (OLD_REGISTRY, NEW_REGISTRY, "backup key registry ownership"),
        (OLD_CHUNK_READ, NEW_CHUNK_READ, "sealed backup chunk read"),
        (OLD_TAG_WRITE, NEW_TAG_WRITE, "sealed backup tag write"),
        (OLD_REPLACE, NEW_REPLACE, "backup destination replacement"),
    ):
        rendered, applied = replace_once(rendered, old, new, label)
        changed = changed or applied
    if changed:
        BACKUP.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "backup module"),
        (SUPPORT, SUPPORT_ANCHOR, "backup support"),
        (EXECUTE, EXECUTE_ANCHOR, "backup execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0:
        raise RuntimeError("backup create validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("backup create validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    values = (
        repair_dependencies(),
        repair_backup_source(),
        repair_product(),
        repair_validator(),
    )
    return any(values)


def main() -> int:
    dependencies_changed = repair_dependencies()
    source_changed = repair_backup_source()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": dependencies_changed
                or source_changed
                or product_changed
                or validator_changed,
                "dependencies_changed": dependencies_changed,
                "ok": True,
                "product_changed": product_changed,
                "source_changed": source_changed,
                "surface": "native-backup-create",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
