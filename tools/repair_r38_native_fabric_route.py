#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from advance_r38_fabric_cache_align_inventory import advance as advance_cache_align_inventory
from advance_r38_fabric_compact_inventory import advance as advance_compact_inventory
from advance_r38_fabric_route_inventory import advance as advance_route_inventory
from repair_r38_fabric_compact_contract import repair as repair_compact_contract

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

CACHE_MODULE = '''#[path = "native_fabric_cache_align.rs"]
mod native_fabric_cache_align;
'''
COMPACT_MODULE = '''#[path = "native_fabric_compact.rs"]
mod native_fabric_compact;
'''
ROUTE_MODULE = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
MODULE_ANCHOR = '''#[path = "native_expansion.rs"]
mod native_expansion;
'''
CACHE_SUPPORT = "        || native_fabric_cache_align::supports(command)\n"
COMPACT_SUPPORT = "        || native_fabric_compact::supports(command)\n"
ROUTE_SUPPORT = "        || native_fabric_route::supports(command)\n"
SUPPORT_ANCHOR = "        || native_expansion::supports(command)\n"
CACHE_EXECUTE = '''    if native_fabric_cache_align::supports(command) {
        return native_fabric_cache_align::execute(&arguments, state_root).map(Some);
    }
'''
COMPACT_EXECUTE = '''    if native_fabric_compact::supports(command) {
        return native_fabric_compact::execute(&arguments, state_root).map(Some);
    }
'''
ROUTE_EXECUTE = '''    if native_fabric_route::supports(command) {
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
        (ROUTE_MODULE, MODULE_ANCHOR, "fabric route module"),
        (CACHE_MODULE, ROUTE_MODULE, "fabric cache-align module"),
        (COMPACT_MODULE, ROUTE_MODULE, "fabric compact module"),
        (ROUTE_SUPPORT, SUPPORT_ANCHOR, "fabric route support"),
        (CACHE_SUPPORT, ROUTE_SUPPORT, "fabric cache-align support"),
        (COMPACT_SUPPORT, ROUTE_SUPPORT, "fabric compact support"),
        (ROUTE_EXECUTE, EXECUTE_ANCHOR, "fabric route execute"),
        (CACHE_EXECUTE, ROUTE_EXECUTE, "fabric cache-align execute"),
        (COMPACT_EXECUTE, ROUTE_EXECUTE, "fabric compact execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied

    invariants = {
        "cache_module": rendered.count(CACHE_MODULE),
        "cache_support": rendered.count(CACHE_SUPPORT),
        "cache_execute": rendered.count(CACHE_EXECUTE),
        "compact_module": rendered.count(COMPACT_MODULE),
        "compact_support": rendered.count(COMPACT_SUPPORT),
        "compact_execute": rendered.count(COMPACT_EXECUTE),
        "route_module": rendered.count(ROUTE_MODULE),
        "route_support": rendered.count(ROUTE_SUPPORT),
        "route_execute": rendered.count(ROUTE_EXECUTE),
    }
    if invariants != {
        "cache_module": 1,
        "cache_support": 1,
        "cache_execute": 1,
        "compact_module": 1,
        "compact_support": 1,
        "compact_execute": 1,
        "route_module": 1,
        "route_support": 1,
        "route_execute": 1,
    }:
        raise RuntimeError(f"fabric route wiring invariant failed: {invariants}")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    contract_changed = repair_compact_contract()
    wiring_changed = repair()
    route_inventory_changed = advance_route_inventory()
    cache_align_inventory_changed = advance_cache_align_inventory()
    compact_inventory_changed = advance_compact_inventory()
    print(
        json.dumps(
            {
                "cache_align_inventory_changed": cache_align_inventory_changed,
                "changed": (
                    contract_changed
                    or wiring_changed
                    or route_inventory_changed
                    or cache_align_inventory_changed
                    or compact_inventory_changed
                ),
                "compact_contract_changed": contract_changed,
                "compact_inventory_changed": compact_inventory_changed,
                "ok": True,
                "route_inventory_changed": route_inventory_changed,
                "surface": "native-fabric-control-plane",
                "wiring_changed": wiring_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
