#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "crates" / "syntavra-cli" / "src" / "native_backup_verify.rs"
RESTORE = ROOT / "crates" / "syntavra-cli" / "src" / "native_backup_restore.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

EXPOSE = (
    "open_sealed_file",
    "safe_path",
    "safe_extract",
    "verify_extracted",
)

MODULE = '''#[path = "native_backup_restore.rs"]
mod native_backup_restore;
'''
MODULE_ANCHOR = '''#[path = "native_backup_verify.rs"]
mod native_backup_verify;
'''
SUPPORT = "        || native_backup_restore::supports(command)\n"
SUPPORT_ANCHOR = "        || native_backup_verify::supports(command)\n"
EXECUTE = '''    if native_backup_restore::supports(command) {
        return native_backup_restore::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_backup_verify::supports(command) {
        let value = native_backup_verify::execute(&arguments, project_root, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
WINDOWS_REPLACE = '''        #[cfg(windows)]
        if fs::symlink_metadata(target).is_ok() {
            fs::remove_file(target)
                .map_err(|error| format!("BACKUP_RESTORE_TARGET_REMOVE_FAILED:{error}"))?;
        }
'''
WINDOWS_REPLACE_ANCHOR = '''        if target.is_dir() {
            return Err("BACKUP_RESTORE_TARGET_IS_DIRECTORY".to_owned());
        }
'''
TARGET = '    "tests/runtime/test_native_backup_restore_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_backup_verify_r38.py",\n'


def insert_after(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    if source.count(token) == 1:
        return source, False
    if source.count(token) != 0 or source.count(anchor) != 1:
        raise RuntimeError(f"{label} contract is ambiguous")
    return source.replace(anchor, anchor + token, 1), True


def expose_verify_helpers() -> bool:
    source = VERIFY.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for name in EXPOSE:
        public = re.compile(rf"(?m)^pub\(crate\) fn {re.escape(name)}\s*\(")
        private = re.compile(rf"(?m)^fn {re.escape(name)}\s*\(")
        if public.search(rendered):
            continue
        matches = list(private.finditer(rendered))
        if len(matches) != 1:
            raise RuntimeError(f"backup verify helper exposure is ambiguous: {name}")
        rendered = private.sub(f"pub(crate) fn {name}(", rendered, count=1)
        changed = True
    if changed:
        VERIFY.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_restore_source() -> bool:
    source = RESTORE.read_text(encoding="utf-8")
    rendered, changed = insert_after(
        source,
        WINDOWS_REPLACE,
        WINDOWS_REPLACE_ANCHOR,
        "backup restore Windows replacement",
    )
    if changed:
        RESTORE.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "backup restore module"),
        (SUPPORT, SUPPORT_ANCHOR, "backup restore support"),
        (EXECUTE, EXECUTE_ANCHOR, "backup restore execute"),
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
        raise RuntimeError("backup restore validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    verify_changed = expose_verify_helpers()
    restore_changed = repair_restore_source()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": verify_changed or restore_changed or product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "restore_changed": restore_changed,
                "surface": "native-backup-restore",
                "validator_changed": validator_changed,
                "verify_changed": verify_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
