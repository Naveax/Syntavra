#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTES = (
    "run competitive-doctor",
    "run competitive-status",
    "run platform-doctor",
    "run platform-status",
)
BASE_COUNT = 153
TARGET_COUNT = 157


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError("native command inventory must be sorted and unique")
    missing = [route for route in ROUTES if route not in native]
    if missing:
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"platform health advance requires {BASE_COUNT} native routes, got {len(native)}"
            )
        if len(missing) != len(ROUTES):
            raise RuntimeError(f"platform health route set is partially certified: {missing}")
        native = sorted([*native, *ROUTES])
        changed = True
    else:
        if len(native) < TARGET_COUNT:
            raise RuntimeError("platform health routes present below certified floor")
        changed = False
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
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "changed": changed,
                "certified_floor": TARGET_COUNT,
                "native_public_command_count": len(
                    contract["rust_surface"]["native_public_commands"]
                ),
                "ok": True,
                "routes": list(ROUTES),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
