#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"
TEST = ROOT / "tests" / "runtime" / "test_native_fabric_install_r38.py"
ANCHOR = '    "tests/runtime/test_native_fabric_insights_r38.py",\n'
TOKEN = '    "tests/runtime/test_native_fabric_install_r38.py",\n'

OLD_PYTHON_OUTPUT = '''    python_receipt = _json_stdout(python)
    python_value = json.loads(output.read_text(encoding="utf-8"))
'''
NEW_PYTHON_OUTPUT = '''    python_receipt = _json_stdout(python)
    python_payload = output.read_bytes()
    python_value = json.loads(python_payload)
'''
OLD_RUST_OUTPUT = '''    rust_receipt = _json_stdout(rust)
    rust_value = json.loads(output.read_text(encoding="utf-8"))
    assert rust_receipt == python_receipt
'''
NEW_RUST_OUTPUT = '''    rust_receipt = _json_stdout(rust)
    rust_payload = output.read_bytes()
    rust_value = json.loads(rust_payload)
    assert python_receipt["ok"] is True
    assert rust_receipt["ok"] is True
    assert python_receipt["output"] == rust_receipt["output"] == str(output)
    assert python_receipt["bytes"] == len(python_payload)
    assert rust_receipt["bytes"] == len(rust_payload)
'''


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TOKEN) == 1:
        return False
    if source.count(TOKEN) != 0:
        raise RuntimeError("fabric install validator target count invalid")
    if source.count(ANCHOR) != 1:
        raise RuntimeError("fabric install validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(ANCHOR, ANCHOR + TOKEN, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in source:
        if old in source:
            raise RuntimeError(f"legacy {label} remains beside canonical form")
        return source, False
    if source.count(old) != 1:
        raise RuntimeError(f"{label} token must be unique")
    return source.replace(old, new, 1), True


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    rendered, python_changed = replace_once(
        source,
        OLD_PYTHON_OUTPUT,
        NEW_PYTHON_OUTPUT,
        "fabric install Python output receipt",
    )
    rendered, rust_changed = replace_once(
        rendered,
        OLD_RUST_OUTPUT,
        NEW_RUST_OUTPUT,
        "fabric install Rust output receipt",
    )
    changed = python_changed or rust_changed
    if changed:
        TEST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    validator_changed = repair_validator()
    test_changed = repair_test()
    return validator_changed or test_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-install-validator",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
