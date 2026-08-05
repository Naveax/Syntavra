#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
ROUTE = "fabric profile"
BASE_COUNT = 146
TARGET_COUNT = 147


def advance() -> bool:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    commands = list(document["rust_surface"]["native_public_commands"])
    if commands != sorted(set(commands)):
        raise RuntimeError("native command inventory must be sorted and unique")
    if ROUTE not in commands:
        if len(commands) != BASE_COUNT:
            raise RuntimeError(
                f"fabric profile requires {BASE_COUNT} prior native routes; "
                f"found {len(commands)}"
            )
        commands = sorted([*commands, ROUTE])
        changed = True
    else:
        if len(commands) < TARGET_COUNT:
            raise RuntimeError("fabric profile is below its certified route floor")
        changed = False
    document["rust_surface"]["native_public_commands"] = commands
    if changed:
        CONTRACT.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return changed


def main() -> int:
    changed = advance()
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "changed": changed,
                "native_public_command_count": len(
                    document["rust_surface"]["native_public_commands"]
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
