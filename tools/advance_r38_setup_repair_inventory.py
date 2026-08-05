#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTES = ("repair", "setup")
BASE_COUNT = 132
TARGET_COUNT = 134


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError("native command inventory must be sorted and unique before setup/repair advance")

    present = tuple(route in native for route in ROUTES)
    if present == (False, False):
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"setup/repair inventory advance requires {BASE_COUNT} native routes, got {len(native)}"
            )
        native = sorted([*native, *ROUTES])
        changed = True
    elif present == (True, True):
        if len(native) < TARGET_COUNT:
            raise RuntimeError(
                f"setup/repair inventory is present below its certified floor: "
                f"native count is {len(native)}, minimum {TARGET_COUNT}"
            )
        changed = False
    else:
        raise RuntimeError(f"partial setup/repair inventory state is forbidden: {present}")

    if len(native) < TARGET_COUNT or native != sorted(set(native)):
        raise RuntimeError(
            "setup/repair inventory must remain sorted, unique and at or above the 134-route certified floor"
        )
    if any(route not in native for route in ROUTES):
        raise RuntimeError("setup/repair certified routes are missing after inventory advance")

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
    native_count = len(contract["rust_surface"]["native_public_commands"])
    print(
        json.dumps(
            {
                "changed": changed,
                "certified_floor": TARGET_COUNT,
                "native_public_command_count": native_count,
                "ok": True,
                "routes": list(ROUTES),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
