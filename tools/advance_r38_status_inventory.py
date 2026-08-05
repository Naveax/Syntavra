#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTE = "status"
BASE_COUNT = 134
TARGET_COUNT = 135


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError("native command inventory must be sorted and unique before status advance")
    present = ROUTE in native
    if not present:
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"status inventory advance requires {BASE_COUNT} native routes, got {len(native)}"
            )
        native = sorted([*native, ROUTE])
        changed = True
    else:
        if len(native) != TARGET_COUNT:
            raise RuntimeError(
                f"status is present but native count is {len(native)}, expected {TARGET_COUNT}"
            )
        changed = False
    if len(native) != TARGET_COUNT or native != sorted(set(native)):
        raise RuntimeError("status inventory target must be 135 sorted unique routes")
    rust["native_public_commands"] = native
    if changed:
        CONTRACT.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return changed


def main() -> int:
    changed = advance()
    print(
        json.dumps(
            {
                "changed": changed,
                "native_public_command_target": TARGET_COUNT,
                "ok": True,
                "route": ROUTE,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
