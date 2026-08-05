#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from advance_r38_fabric_cache_align_inventory import advance as advance_cache_align_inventory
from advance_r38_fabric_compact_inventory import advance as advance_compact_inventory
from advance_r38_fabric_doctor_inventory import advance as advance_doctor_inventory
from advance_r38_fabric_insights_inventory import advance as advance_insights_inventory
from advance_r38_fabric_install_inventory import advance as advance_install_inventory
from advance_r38_fabric_installations_inventory import advance as advance_installations_inventory
from advance_r38_fabric_platform_plan_inventory import advance as advance_platform_plan_inventory
from advance_r38_fabric_profile_inventory import advance as advance_profile_inventory
from advance_r38_fabric_route_inventory import advance as advance_route_inventory
from repair_r38_fabric_compact_contract import repair as repair_compact_contract
from repair_r38_fabric_doctor_contract import repair as repair_doctor_contract
from repair_r38_fabric_insights_contract import repair as repair_insights_contract
from repair_r38_fabric_insights_validator import repair as repair_insights_validator
from repair_r38_fabric_install_contract import repair as repair_install_contract
from repair_r38_fabric_install_validator import repair as repair_install_validator
from repair_r38_fabric_installations_contract import repair as repair_installations_contract
from repair_r38_fabric_installations_validator import repair as repair_installations_validator
from repair_r38_fabric_platform_plan_contract import repair as repair_platform_plan_contract
from repair_r38_fabric_platform_plan_validator import repair as repair_platform_plan_validator
from repair_r38_fabric_profile_contract import repair as repair_profile_contract
from repair_r38_fabric_profile_validator import repair as repair_profile_validator

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
    compact_contract_changed = repair_compact_contract()
    base_wiring_changed = repair()
    doctor_contract_changed = repair_doctor_contract()
    insights_contract_changed = repair_insights_contract()
    insights_validator_changed = repair_insights_validator()
    install_contract_changed = repair_install_contract()
    install_validator_changed = repair_install_validator()
    installations_contract_changed = repair_installations_contract()
    installations_validator_changed = repair_installations_validator()
    platform_plan_contract_changed = repair_platform_plan_contract()
    platform_plan_validator_changed = repair_platform_plan_validator()
    profile_contract_changed = repair_profile_contract()
    profile_validator_changed = repair_profile_validator()
    route_inventory_changed = advance_route_inventory()
    cache_align_inventory_changed = advance_cache_align_inventory()
    compact_inventory_changed = advance_compact_inventory()
    doctor_inventory_changed = advance_doctor_inventory()
    insights_inventory_changed = advance_insights_inventory()
    install_inventory_changed = advance_install_inventory()
    installations_inventory_changed = advance_installations_inventory()
    platform_plan_inventory_changed = advance_platform_plan_inventory()
    profile_inventory_changed = advance_profile_inventory()
    changed = any(
        (
            compact_contract_changed,
            base_wiring_changed,
            doctor_contract_changed,
            insights_contract_changed,
            insights_validator_changed,
            install_contract_changed,
            install_validator_changed,
            installations_contract_changed,
            installations_validator_changed,
            platform_plan_contract_changed,
            platform_plan_validator_changed,
            profile_contract_changed,
            profile_validator_changed,
            route_inventory_changed,
            cache_align_inventory_changed,
            compact_inventory_changed,
            doctor_inventory_changed,
            insights_inventory_changed,
            install_inventory_changed,
            installations_inventory_changed,
            platform_plan_inventory_changed,
            profile_inventory_changed,
        )
    )
    print(
        json.dumps(
            {
                "cache_align_inventory_changed": cache_align_inventory_changed,
                "changed": changed,
                "compact_contract_changed": compact_contract_changed,
                "compact_inventory_changed": compact_inventory_changed,
                "doctor_contract_changed": doctor_contract_changed,
                "doctor_inventory_changed": doctor_inventory_changed,
                "insights_contract_changed": insights_contract_changed,
                "insights_inventory_changed": insights_inventory_changed,
                "insights_validator_changed": insights_validator_changed,
                "install_contract_changed": install_contract_changed,
                "install_inventory_changed": install_inventory_changed,
                "install_validator_changed": install_validator_changed,
                "installations_contract_changed": installations_contract_changed,
                "installations_inventory_changed": installations_inventory_changed,
                "installations_validator_changed": installations_validator_changed,
                "platform_plan_contract_changed": platform_plan_contract_changed,
                "platform_plan_inventory_changed": platform_plan_inventory_changed,
                "platform_plan_validator_changed": platform_plan_validator_changed,
                "profile_contract_changed": profile_contract_changed,
                "profile_inventory_changed": profile_inventory_changed,
                "profile_validator_changed": profile_validator_changed,
                "ok": True,
                "route_inventory_changed": route_inventory_changed,
                "surface": "native-fabric-control-plane",
                "wiring_changed": (
                    base_wiring_changed
                    or doctor_contract_changed
                    or insights_contract_changed
                    or install_contract_changed
                    or installations_contract_changed
                    or platform_plan_contract_changed
                    or profile_contract_changed
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
