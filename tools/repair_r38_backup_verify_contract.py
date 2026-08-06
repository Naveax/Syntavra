#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "crates" / "syntavra-cli" / "src" / "native_backup.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

EXPOSE = (
    ("fn unique_temp_root()", "pub(crate) fn unique_temp_root()"),
    ("fn sha256_file(path: &Path)", "pub(crate) fn sha256_file(path: &Path)"),
    ("fn set_private(path: &Path)", "pub(crate) fn set_private(path: &Path)"),
    (
        "fn decode_environment_key(value: &str)",
        "pub(crate) fn decode_environment_key(value: &str)",
    ),
    (
        "fn derive_key(master_key: &[u8; 32]",
        "pub(crate) fn derive_key(master_key: &[u8; 32]",
    ),
    (
        "fn initialize_evidence_state(state_root: &Path)",
        "pub(crate) fn initialize_evidence_state(state_root: &Path)",
    ),
    (
        "fn initialize_roots(state_root: &Path)",
        "pub(crate) fn initialize_roots(state_root: &Path)",
    ),
)

MODULE = '''#[path = "native_backup_verify.rs"]
mod native_backup_verify;
'''
MODULE_ANCHOR = '''#[path = "native_backup.rs"]
mod native_backup;
'''
SUPPORT = "        || native_backup_verify::supports(command)\n"
SUPPORT_ANCHOR = "        || native_backup::supports(command)\n"
EXECUTE = '''    if native_backup_verify::supports(command) {
        let value = native_backup_verify::execute(&arguments, project_root, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
EXECUTE_ANCHOR = '''    if native_backup::supports(command) {
        return native_backup::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_backup_verify_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_backup_create_r38.py",\n'


def insert_after(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    if source.count(token) == 1:
        return source, False
    if source.count(token) != 0 or source.count(anchor) != 1:
        raise RuntimeError(f"{label} contract is ambiguous")
    return source.replace(anchor, anchor + token, 1), True


def expose_backup_helpers() -> bool:
    source = BACKUP.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for private, public in EXPOSE:
        if public in rendered:
            continue
        if rendered.count(private) != 1:
            raise RuntimeError(f"backup helper exposure is ambiguous: {private}")
        rendered = rendered.replace(private, public, 1)
        changed = True
    if changed:
        BACKUP.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "backup verify module"),
        (SUPPORT, SUPPORT_ANCHOR, "backup verify support"),
        (EXECUTE, EXECUTE_ANCHOR, "backup verify execute"),
    ):
        rendered, applied = insert_after(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("backup verify validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    backup_changed = expose_backup_helpers()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "backup_changed": backup_changed,
                "changed": backup_changed or product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "surface": "native-backup-verify",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
