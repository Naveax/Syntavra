#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "validate_r38_regression_closure.py"
ANCHOR = '    "tests/runtime/test_native_fabric_insights_r38.py",\n'
TOKEN = '    "tests/runtime/test_native_fabric_install_r38.py",\n'


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    if source.count(TOKEN) == 1:
        return False
    if source.count(TOKEN) != 0:
        raise RuntimeError("fabric install validator target count invalid")
    if source.count(ANCHOR) != 1:
        raise RuntimeError("fabric install validator anchor must be unique")
    TARGET.write_text(
        source.replace(ANCHOR, ANCHOR + TOKEN, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


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
