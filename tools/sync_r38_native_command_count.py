#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
SELECTOR = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra.rs"
PATTERN = re.compile(r"const NATIVE_COMMAND_COUNT: u64 = (?P<count>[0-9]+);")


def sync() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = int(contract["rust_surface"]["native_public_command_count"])
    source = SELECTOR.read_text(encoding="utf-8")
    matches = list(PATTERN.finditer(source))
    if len(matches) != 1:
        raise RuntimeError(f"expected one native command count constant, found {len(matches)}")
    current = int(matches[0].group("count"))
    rendered = PATTERN.sub(f"const NATIVE_COMMAND_COUNT: u64 = {expected};", source, count=1)
    if rendered != source:
        SELECTOR.write_text(rendered, encoding="utf-8", newline="\n")
    print(json.dumps({"current": current, "expected": expected, "changed": current != expected}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(sync())
