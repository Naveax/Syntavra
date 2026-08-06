#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_CLI = ROOT / "syntavra_runtime" / "platform_cli.py"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

OLD_LOAD = '''def _load(value: str) -> Any:
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)
'''
NEW_LOAD = '''def _load(value: str) -> Any:
    path = Path(value)
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # Long inline JSON is data, not a filesystem path. Path.is_file() may
        # raise ENAMETOOLONG before the JSON parser gets a chance to consume it.
        pass
    return json.loads(value)
'''

MODULE = '''#[path = "native_run_adapter_certify.rs"]
mod native_run_adapter_certify;
'''
MODULE_ANCHOR = '''#[path = "native_run_adapter_conformance.rs"]
mod native_run_adapter_conformance;
'''
SUPPORT = "        || native_run_adapter_certify::supports(command)\n"
SUPPORT_ANCHOR = "        || native_run_adapter_conformance::supports(command)\n"
EXECUTE = '''    if native_run_adapter_certify::supports(command) {
        let value = native_run_adapter_certify::execute(&arguments, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
EXECUTE_ANCHOR = '''    if native_run_adapter_conformance::supports(command) {
        return native_run_adapter_conformance::execute(&arguments, project_root, state_root)
            .map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_adapter_certify_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_adapter_conformance_r38.py",\n'


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_platform_cli() -> bool:
    source = PLATFORM_CLI.read_text(encoding="utf-8")
    if source.count(NEW_LOAD) == 1:
        return False
    if source.count(NEW_LOAD) != 0:
        raise RuntimeError("safe platform JSON loader count invalid")
    if source.count(OLD_LOAD) != 1:
        raise RuntimeError("legacy platform JSON loader must be unique")
    PLATFORM_CLI.write_text(
        source.replace(OLD_LOAD, NEW_LOAD, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "adapter certify module"),
        (SUPPORT, SUPPORT_ANCHOR, "adapter certify support"),
        (EXECUTE, EXECUTE_ANCHOR, "adapter certify execute"),
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
        raise RuntimeError("adapter certify validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("adapter certify validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    values = (repair_platform_cli(), repair_product(), repair_validator())
    return any(values)


def main() -> int:
    platform_cli_changed = repair_platform_cli()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(json.dumps({
        "changed": platform_cli_changed or product_changed or validator_changed,
        "ok": True,
        "platform_cli_changed": platform_cli_changed,
        "product_changed": product_changed,
        "surface": "native-run-adapter-certify",
        "validator_changed": validator_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
