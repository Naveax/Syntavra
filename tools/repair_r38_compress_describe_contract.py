#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_compress_describe.rs"]
mod native_compress_describe;
'''
MODULE_ANCHOR = '''#[path = "native_backup_restore.rs"]
mod native_backup_restore;
'''
SUPPORT = "        || native_compress_describe::supports(command)\n"
SUPPORT_ANCHOR = "        || native_backup_restore::supports(command)\n"
EXECUTE = '''    if native_compress_describe::supports(command) {
        return native_compress_describe::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_backup_restore::supports(command) {
        return native_backup_restore::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_compress_describe_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_backup_restore_r38.py",\n'


def insert_after(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    if source.count(token) == 1:
        return source, False
    if source.count(token) != 0 or source.count(anchor) != 1:
        raise RuntimeError(f"{label} contract is ambiguous")
    return source.replace(anchor, anchor + token, 1), True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "compress describe module"),
        (SUPPORT, SUPPORT_ANCHOR, "compress describe support"),
        (EXECUTE, EXECUTE_ANCHOR, "compress describe execute"),
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
        raise RuntimeError("compress describe validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "surface": "native-compress-describe",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
