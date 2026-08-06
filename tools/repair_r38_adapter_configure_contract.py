#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
RUN_ADAPTERS = ROOT / "crates" / "syntavra-cli" / "src" / "native_run_adapters.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_adapter_configure.rs"]
mod native_adapter_configure;
'''
MODULE_ANCHOR = '''#[path = "native_run_adapters.rs"]
mod native_run_adapters;
'''
SUPPORT = "        || native_adapter_configure::supports(command)\n"
SUPPORT_ANCHOR = "        || native_run_adapters::supports(command)\n"
EXECUTE = '''    if native_adapter_configure::supports(command) {
        return native_adapter_configure::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_run_adapters::supports(command) {
        return native_run_adapters::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_adapter_configure_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_adapters_r38.py",\n'
SHARED_HELPER_GUARDS = (
    (
        "pub(crate) fn contract(adapter_id: &str) -> Result<Value, String> {",
        "#[allow(dead_code)]\npub(crate) fn contract(adapter_id: &str) -> Result<Value, String> {",
        "adapter contract helper guard",
    ),
    (
        "pub(crate) fn detection(adapter_id: &str, project_root: &Path) -> Result<Value, String> {",
        "#[allow(dead_code)]\npub(crate) fn detection(adapter_id: &str, project_root: &Path) -> Result<Value, String> {",
        "adapter detection helper guard",
    ),
)


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
        (MODULE, MODULE_ANCHOR, "adapter configure module"),
        (SUPPORT, SUPPORT_ANCHOR, "adapter configure support"),
        (EXECUTE, EXECUTE_ANCHOR, "adapter configure execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def guard_shared_helpers() -> bool:
    source = RUN_ADAPTERS.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for unguarded, guarded, label in SHARED_HELPER_GUARDS:
        guarded_count = rendered.count(guarded)
        if guarded_count == 1:
            continue
        if guarded_count != 0:
            raise RuntimeError(f"{label} count invalid: {guarded_count}")
        if rendered.count(unguarded) != 1:
            raise RuntimeError(f"{label} source must be unique")
        rendered = rendered.replace(unguarded, guarded, 1)
        changed = True
    if changed:
        RUN_ADAPTERS.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def wire_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0:
        raise RuntimeError("adapter configure validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("adapter configure validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    product_changed = wire_product()
    shared_helpers_changed = guard_shared_helpers()
    validator_changed = wire_validator()
    return product_changed or shared_helpers_changed or validator_changed


def main() -> int:
    product_changed = wire_product()
    shared_helpers_changed = guard_shared_helpers()
    validator_changed = wire_validator()
    print(
        json.dumps(
            {
                "changed": product_changed or shared_helpers_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "shared_helpers_changed": shared_helpers_changed,
                "surface": "native-adapter-configure",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
