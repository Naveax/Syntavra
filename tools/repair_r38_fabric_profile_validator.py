#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"
TEST = ROOT / "tests" / "runtime" / "test_native_fabric_profile_r38.py"
ANCHOR = '    "tests/runtime/test_native_fabric_platform_plan_r38.py",\n'
TOKEN = '    "tests/runtime/test_native_fabric_profile_r38.py",\n'
OLD_ORDER = '''    assert value["selected_tools"][:2] == [
        "syntavra.status",
        "syntavra.output.capture",
    ]
'''
NEW_ORDER = '''    assert value["selected_tools"][0] == "syntavra.status"
    assert "syntavra.inspect.map" in value["selected_tools"]
    assert "syntavra.output.capture" in value["selected_tools"]
'''


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TOKEN) == 1:
        return False
    if source.count(TOKEN) != 0:
        raise RuntimeError("fabric profile validator target count invalid")
    if source.count(ANCHOR) != 1:
        raise RuntimeError("fabric profile validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(ANCHOR, ANCHOR + TOKEN, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    if NEW_ORDER in source:
        if OLD_ORDER in source:
            raise RuntimeError("legacy fabric profile ordering assertion remains")
        return False
    if source.count(OLD_ORDER) != 1:
        raise RuntimeError("fabric profile ordering assertion contract not found")
    TEST.write_text(
        source.replace(OLD_ORDER, NEW_ORDER, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


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
                "surface": "native-fabric-profile-validator",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
