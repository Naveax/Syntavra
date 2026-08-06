#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_run_adapters.rs"]
mod native_run_adapters;
'''
MODULE_ANCHOR = '''#[path = "native_route.rs"]
mod native_route;
'''
SUPPORT = "        || native_run_adapters::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_verify_install::supports(command)\n"
EXECUTE = '''    if native_run_adapters::supports(command) {
        return native_run_adapters::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_fabric_route::supports(command) {
        return native_fabric_route::execute(&arguments, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_adapters_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_fabric_verify_install_r38.py",\n'


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def wire_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "run adapters module"),
        (SUPPORT, SUPPORT_ANCHOR, "run adapters support"),
        (EXECUTE, EXECUTE_ANCHOR, "run adapters execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def wire_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0:
        raise RuntimeError("run adapters validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("run adapters validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    product_changed = wire_product()
    validator_changed = wire_validator()
    return product_changed or validator_changed


def main() -> int:
    product_changed = wire_product()
    validator_changed = wire_validator()
    print(json.dumps({
        "changed": product_changed or validator_changed,
        "ok": True,
        "product_changed": product_changed,
        "surface": "native-run-adapters",
        "validator_changed": validator_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
