#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
DESCRIBE = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_describe.rs"
EVIDENCE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_store.rs"
VERIFY = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_verify.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_compress_verify.rs"]
mod native_compress_verify;
'''
MODULE_ANCHOR = '''#[path = "native_compress_put.rs"]
mod native_compress_put;
'''
SUPPORT = "        || native_compress_verify::supports(command)\n"
SUPPORT_ANCHOR = "        || native_compress_put::supports(command)\n"
EXECUTE = '''    if native_compress_verify::supports(command) {
        let value = native_compress_verify::execute(&arguments, project_root, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
EXECUTE_ANCHOR = '''    if native_compress_put::supports(command) {
        return native_compress_put::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_compress_verify_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_compress_get_r38.py",\n'


def validate_sources() -> None:
    for path in (VERIFY, DESCRIBE, EVIDENCE):
        if not path.is_file():
            raise RuntimeError(f"compress verify dependency is missing: {path}")
    describe = DESCRIBE.read_text(encoding="utf-8")
    for marker in (
        "pub(crate) fn initialize_database",
        "pub(crate) fn describe",
    ):
        if marker not in describe:
            raise RuntimeError(f"compress verify dependency is missing: {marker}")
    evidence = EVIDENCE.read_text(encoding="utf-8")
    if "pub(crate) fn get" not in evidence:
        raise RuntimeError("compress verify evidence get dependency is missing")


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    module_marker = "mod native_compress_verify;"
    if module_marker not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("compress verify module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + MODULE, 1)
        changed = True

    support_marker = "|| native_compress_verify::supports(command)"
    if support_marker not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("compress verify support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    execute_support = "if native_compress_verify::supports(command) {"
    execute_call = "native_compress_verify::execute("
    execute_presence = (execute_support in rendered, execute_call in rendered)
    if execute_presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("compress verify execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif execute_presence != (True, True):
        raise RuntimeError("compress verify execute wiring is partial")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("compress verify validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    validate_sources()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "surface": "native-compress-verify",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
