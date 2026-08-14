#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"
TARGET = '    "tests/runtime/test_native_fabric_rollback_install_r38.py",\n'
ANCHOR = '    "tests/runtime/test_native_fabric_installations_r38.py",\n'


def repair() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    count = source.count(TARGET)
    if count == 1:
        return False
    if count != 0:
        raise RuntimeError(f"rollback validator target count invalid: {count}")
    if source.count(ANCHOR) != 1:
        raise RuntimeError("rollback validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(ANCHOR, ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    changed = repair()
    print(json.dumps({
        "changed": changed,
        "ok": True,
        "surface": "native-fabric-rollback-install-validator",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
