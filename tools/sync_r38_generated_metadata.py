#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import sync_r38_native_command_count as legacy

EXPANSION_DECLARATION = '#[path = "native_expansion.rs"]\nmod native_expansion;\n'
EXPANSION_ANCHOR = '#[path = "native_external_suite_gate.rs"]\nmod native_external_suite_gate;\n'
REQUIRED_NATIVE_COMMANDS = {
    "output compact",
    "output govern",
}


def normalize_native_expansion(path: Path = legacy.NATIVE_PRODUCT) -> bool:
    source = path.read_text(encoding="utf-8")
    without_expansion = source.replace(EXPANSION_DECLARATION, "")
    anchor_count = without_expansion.count(EXPANSION_ANCHOR)
    if anchor_count != 1:
        raise RuntimeError(
            f"expected one native expansion anchor, found {anchor_count}"
        )
    rendered = without_expansion.replace(
        EXPANSION_ANCHOR,
        EXPANSION_DECLARATION + EXPANSION_ANCHOR,
        1,
    )
    changed = rendered != source
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def ensure_required_native_commands(path: Path = legacy.CONTRACT) -> bool:
    contract = json.loads(path.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    commands = set(rust["native_public_commands"])
    missing = REQUIRED_NATIVE_COMMANDS - commands
    if not missing:
        return False
    commands.update(REQUIRED_NATIVE_COMMANDS)
    rust["native_public_commands"] = sorted(commands)
    path.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def synchronize() -> int:
    ensure_required_native_commands()
    normalize_native_expansion()
    status = legacy.sync()
    normalize_native_expansion()
    return status


if __name__ == "__main__":
    raise SystemExit(synchronize())
