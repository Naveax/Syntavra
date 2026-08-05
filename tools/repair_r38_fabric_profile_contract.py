#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_profile.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

OLD_RESULT = '''    let result = json!({
        "profile": profile,
        "purpose": purpose,
        "selected_tools": selected,
        "selected_count": selected.len(),
        "available_count": available.len(),
        "omitted_count": available.len().saturating_sub(selected.len()),
        "estimated_manifest_tokens": estimated,
        "manifest_budget": budget,
        "within_budget": estimated <= budget || profile == "audit",
        "host": host,
        "host_mode": host_contract["negotiation"]["mode"],
        "profile_hash": profile_hash(&profile, &selected)?,
    });
'''
NEW_RESULT = '''    let selected_count = selected.len();
    let available_count = available.len();
    let omitted_count = available_count.saturating_sub(selected_count);
    let within_budget = estimated <= budget || profile == "audit";
    let hash = profile_hash(&profile, &selected)?;
    let result = json!({
        "profile": profile,
        "purpose": purpose,
        "selected_tools": selected,
        "selected_count": selected_count,
        "available_count": available_count,
        "omitted_count": omitted_count,
        "estimated_manifest_tokens": estimated,
        "manifest_budget": budget,
        "within_budget": within_budget,
        "host": host,
        "host_mode": host_contract["negotiation"]["mode"],
        "profile_hash": hash,
    });
'''
RESULT_SIGNATURE = "let selected_count = selected.len();"

MODULE = '''#[path = "native_fabric_profile.rs"]
mod native_fabric_profile;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_platform_plan.rs"]
mod native_fabric_platform_plan;
'''
SUPPORT = "        || native_fabric_profile::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_platform_plan::supports(command)\n"
EXECUTE = '''    if native_fabric_profile::supports(command) {
        return native_fabric_profile::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_SIGNATURE = "native_fabric_profile::execute(&arguments"
EXECUTE_ANCHOR_SIGNATURE = "    if native_fabric_platform_plan::supports(command) {\n"


def insert_once(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_profile_source() -> bool:
    source = PROFILE.read_text(encoding="utf-8")
    if RESULT_SIGNATURE in source:
        if OLD_RESULT in source:
            raise RuntimeError("legacy fabric profile result construction remains")
        return False
    if source.count(OLD_RESULT) != 1:
        raise RuntimeError("fabric profile result construction contract not found")
    PROFILE.write_text(
        source.replace(OLD_RESULT, NEW_RESULT, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "fabric profile module"),
        (SUPPORT, SUPPORT_ANCHOR, "fabric profile support"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied

    execute_count = rendered.count(EXECUTE_SIGNATURE)
    if execute_count == 0:
        if rendered.count(EXECUTE_ANCHOR_SIGNATURE) != 1:
            raise RuntimeError("fabric profile semantic execute anchor must be unique")
        rendered = rendered.replace(
            EXECUTE_ANCHOR_SIGNATURE,
            EXECUTE + EXECUTE_ANCHOR_SIGNATURE,
            1,
        )
        changed = True
    elif execute_count != 1:
        raise RuntimeError(f"fabric profile execute count invalid: {execute_count}")

    if rendered.count(MODULE) != 1 or rendered.count(SUPPORT) != 1:
        raise RuntimeError("fabric profile wiring invariant failed")
    if rendered.count(EXECUTE_SIGNATURE) != 1:
        raise RuntimeError("fabric profile semantic execute invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    profile_changed = repair_profile_source()
    product_changed = repair_product()
    return profile_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-profile-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
