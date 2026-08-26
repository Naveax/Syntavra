#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SURFACE = ROOT / "contracts/engine/dual-engine-public-surface-v2.json"

surface = json.loads(SURFACE.read_text(encoding="utf-8"))
python_surface = surface.get("python_surface") or {}
count = int(python_surface.get("public_command_count", -1))
if count != 245:
    raise AssertionError(f"unexpected canonical Python public route count: {count}")

text = README.read_text(encoding="utf-8")
old = (
    "The complete Python surface currently contains 257 public command paths. "
    "Full dual-engine parity is tracked against all 257 paths in `contracts/engine/dual-engine-public-surface-v2.json`; "
)
new = (
    "The canonical installed Python surface currently contains 245 executable public command paths. "
    "Full dual-engine parity inventory is tracked against those 245 paths in `contracts/engine/dual-engine-public-surface-v2.json`; "
)
if text.count(old) != 1:
    raise AssertionError("README public-surface count text drift")
README.write_text(text.replace(old, new, 1), encoding="utf-8")

print(json.dumps({
    "canonical_python_public_command_count": count,
    "source": "contracts/engine/dual-engine-public-surface-v2.json",
    "readme_synchronized": True,
}, indent=2))
