#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "repair_r38_runtime_regressions.py"

OLD = '''SINGLE_SEGMENT_PATH_CANONICAL = (
    'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
)
'''
NEW = '''SINGLE_SEGMENT_PATH_CANONICAL = (
    'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init" | "hook" | "mcp")'
)
'''


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    old_count = source.count(OLD)
    new_count = source.count(NEW)
    if old_count == 1 and new_count == 0:
        rendered = source.replace(OLD, NEW, 1)
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
        return True
    if old_count == 0 and new_count == 1:
        return False
    raise RuntimeError(
        "runtime selector canonical constant must be old or current: "
        f"old={old_count}, current={new_count}"
    )


def main() -> int:
    changed = repair()
    print(json.dumps({"changed": changed, "ok": True, "surface": "runtime-selector-contract"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
