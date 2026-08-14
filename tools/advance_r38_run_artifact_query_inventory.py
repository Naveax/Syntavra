#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTE = "run artifact-query"
BASE_COUNT = 167
TARGET_COUNT = 168
PUBLIC_COUNT = 245


def advance() -> bool:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    python = contract["python_surface"]
    rust = contract["rust_surface"]
    if python["public_command_count"] != PUBLIC_COUNT:
        raise RuntimeError(
            f"artifact query advance requires {PUBLIC_COUNT} Python routes, "
            f"got {python['public_command_count']}"
        )
    if rust["python_launcher_bridge_command_count"] != 0:
        raise RuntimeError("artifact query advance forbids Python launcher bridge routes")

    native = list(rust["native_public_commands"])
    if native != sorted(set(native)):
        raise RuntimeError("native command inventory must be sorted and unique")

    if ROUTE not in native:
        if len(native) != BASE_COUNT:
            raise RuntimeError(
                f"artifact query advance requires {BASE_COUNT} native routes, got {len(native)}"
            )
        native = sorted([*native, ROUTE])
        changed = True
    else:
        if len(native) < TARGET_COUNT:
            raise RuntimeError(
                f"artifact query present below certified floor {TARGET_COUNT}: "
                f"got {len(native)}"
            )
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
                "route": ROUTE,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
