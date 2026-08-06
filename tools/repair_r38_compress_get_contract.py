#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
DESCRIBE = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_describe.rs"
GET = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_get.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_compress_get.rs"]
mod native_compress_get;
'''
MODULE_ANCHOR = '''#[path = "native_compress_describe.rs"]
mod native_compress_describe;
'''
SUPPORT = "        || native_compress_get::supports(command)\n"
SUPPORT_ANCHOR = "        || native_compress_describe::supports(command)\n"
EXECUTE = '''    if native_compress_get::supports(command) {
        return native_compress_get::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_compress_describe::supports(command) {
        return native_compress_describe::execute(&arguments, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_compress_get_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_compress_put_r38.py",\n'


def validate_sources() -> None:
    if not GET.is_file():
        raise RuntimeError("native compress get source is missing")
    describe = DESCRIBE.read_text(encoding="utf-8")
    for marker in (
        "pub(crate) fn initialize_database",
        "pub(crate) fn describe",
    ):
        if marker not in describe:
            raise RuntimeError(f"compress get dependency is missing: {marker}")


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    module_marker = "mod native_compress_get;"
    if module_marker not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("compress get module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + MODULE, 1)
        changed = True

    support_marker = "|| native_compress_get::supports(command)"
    if support_marker not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("compress get support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    execute_support = "if native_compress_get::supports(command) {"
    execute_call = "native_compress_get::execute("
    execute_presence = (execute_support in rendered, execute_call in rendered)
    if execute_presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("compress get execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif execute_presence != (True, True):
        raise RuntimeError("compress get execute wiring is partial")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("compress get validator contract is ambiguous")
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
                "surface": "native-compress-get",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
