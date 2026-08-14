#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTE = "fabric doctor"
BASE_COUNT = 141
TARGET_COUNT = 142


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError(
            "native command inventory must be sorted and unique before fabric doctor advance"
        )

    if ROUTE not in native:
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"fabric doctor inventory advance requires {BASE_COUNT} native routes, "
                f"got {len(native)}"
            )
        native = sorted([*native, ROUTE])
        changed = True
    else:
        if len(native) < TARGET_COUNT:
            raise RuntimeError(
                "fabric doctor inventory is present below its certified floor: "
                f"native count is {len(native)}, minimum {TARGET_COUNT}"
            )
        changed = False

    if ROUTE not in native or len(native) < TARGET_COUNT:
        raise RuntimeError("fabric doctor inventory advance failed")
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
                "route": ROUTE,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
