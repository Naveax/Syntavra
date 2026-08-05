#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

MODULE = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
MODULE_ANCHOR = '''#[path = "native_expansion.rs"]
mod native_expansion;
'''
SUPPORT = "        || native_fabric_route::supports(command)\n"
SUPPORT_ANCHOR = "        || native_expansion::supports(command)\n"
EXECUTE = '''    if native_fabric_route::supports(command) {
        return native_fabric_route::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_expansion::supports(command) {
        return native_expansion::execute(command, &arguments, project_root, state_root).map(Some);
    }
'''


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "fabric route module"),
        (SUPPORT, SUPPORT_ANCHOR, "fabric route support"),
        (EXECUTE, EXECUTE_ANCHOR, "fabric route execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied

    invariants = {
        "module": rendered.count(MODULE),
        "support": rendered.count(SUPPORT),
        "execute": rendered.count(EXECUTE),
    }
    if invariants != {"module": 1, "support": 1, "execute": 1}:
        raise RuntimeError(f"fabric route wiring invariant failed: {invariants}")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {"changed": changed, "ok": True, "surface": "native-fabric-route"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
