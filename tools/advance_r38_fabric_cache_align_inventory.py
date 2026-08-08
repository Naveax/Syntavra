#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTE = "fabric cache-align"
BASE_COUNT = 139
TARGET_COUNT = 140


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError(
            "native command inventory must be sorted and unique before fabric cache-align advance"
        )

    present = ROUTE in native
    if not present:
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"fabric cache-align inventory advance requires {BASE_COUNT} native routes, "
                f"got {len(native)}"
            )
        native = sorted([*native, ROUTE])
        changed = True
    else:
        if len(native) < TARGET_COUNT:
            raise RuntimeError(
                "fabric cache-align inventory is present below its certified floor: "
                f"native count is {len(native)}, minimum {TARGET_COUNT}"
            )
        changed = False

    if len(native) < TARGET_COUNT or native != sorted(set(native)):
        raise RuntimeError(
            "fabric cache-align inventory must remain sorted, unique and at or above "
            "the 140-route certified floor"
        )
    if ROUTE not in native:
        raise RuntimeError("fabric cache-align is missing after inventory advance")

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
                "route": ROUTE,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
