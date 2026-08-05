#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "crates" / "syntavra-cli" / "src" / "native_host.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

HOST_CONTRACT = '''pub(crate) fn platform_plan_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    if !matches!(scope, "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    let active = host_specs()
        .into_iter()
        .find(|spec| spec.host == host)
        .ok_or_else(|| format!("unknown host: {host}"))?;
    if host != "generic-mcp" {
        return fabric_install_contract(host, project, scope)
            .map(|contract| contract["plan"].clone());
    }
    let negotiation = negotiate_value(host, true, None);
    Ok(json!({
        "host": active.host,
        "display_name": active.display_name,
        "scope": scope,
        "project": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "enforced": negotiation["enforced"],
        "verified_adapter": active.verified,
        "files": [],
        "capabilities": capabilities(&active),
        "validation": [
            "syntavra doctor",
            format!("syntavra host negotiate --host-name {}", active.host),
            "syntavra status",
        ],
    }))
}

pub(crate) fn all_platform_plan_contracts(
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    if !matches!(scope, "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    let mut hosts = host_specs()
        .into_iter()
        .map(|spec| spec.host)
        .filter(|host| host != "generic-mcp")
        .collect::<Vec<_>>();
    hosts.sort();
    let plans = hosts
        .iter()
        .map(|host| platform_plan_contract(host, project, scope))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "host_count": plans.len(),
        "enforced_count": plans
            .iter()
            .filter(|plan| plan["enforced"].as_bool() == Some(true))
            .count(),
        "verified_count": plans
            .iter()
            .filter(|plan| plan["verified_adapter"].as_bool() == Some(true))
            .count(),
        "hosts": plans,
    }))
}

'''
HOST_ANCHOR = "pub(crate) fn fabric_install_contract(\n"

EXPANSION_BRIDGE = '''pub(crate) fn platform_plan_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    native_host::platform_plan_contract(host, project, scope)
}

pub(crate) fn all_platform_plan_contracts(
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    native_host::all_platform_plan_contracts(project, scope)
}

'''
EXPANSION_ANCHOR = "pub(crate) fn fabric_install_contract(\n"

MODULE = '''#[path = "native_fabric_platform_plan.rs"]
mod native_fabric_platform_plan;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
SUPPORT = "        || native_fabric_platform_plan::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_route::supports(command)\n"
EXECUTE = '''    if native_fabric_platform_plan::supports(command) {
        return native_fabric_platform_plan::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_SIGNATURE = "native_fabric_platform_plan::execute(&arguments"
EXECUTE_ANCHOR = '''    if native_fabric_route::supports(command) {
        return native_fabric_route::execute(&arguments, state_root).map(Some);
    }
'''


def insert_once(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_host() -> bool:
    source = HOST.read_text(encoding="utf-8")
    rendered, changed = insert_once(
        source,
        HOST_CONTRACT,
        HOST_ANCHOR,
        "native host platform plan contract",
    )
    if rendered.count("pub(crate) fn platform_plan_contract") != 1:
        raise RuntimeError("native host platform plan contract invariant failed")
    if rendered.count("pub(crate) fn all_platform_plan_contracts") != 1:
        raise RuntimeError("native host all-platform contract invariant failed")
    if changed:
        HOST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered, changed = insert_once(
        source,
        EXPANSION_BRIDGE,
        EXPANSION_ANCHOR,
        "native expansion platform plan bridge",
    )
    if rendered.count("pub(crate) fn platform_plan_contract") != 1:
        raise RuntimeError("native expansion platform plan bridge invariant failed")
    if rendered.count("pub(crate) fn all_platform_plan_contracts") != 1:
        raise RuntimeError("native expansion all-platform bridge invariant failed")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "fabric platform plan module"),
        (SUPPORT, SUPPORT_ANCHOR, "fabric platform plan support"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    execute_count = rendered.count(EXECUTE_SIGNATURE)
    if execute_count == 0:
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("fabric platform plan execute anchor must be unique")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE + EXECUTE_ANCHOR, 1)
        changed = True
    elif execute_count != 1:
        raise RuntimeError(f"fabric platform plan execute count invalid: {execute_count}")
    if rendered.count(MODULE) != 1 or rendered.count(SUPPORT) != 1:
        raise RuntimeError("fabric platform plan wiring invariant failed")
    if rendered.count(EXECUTE_SIGNATURE) != 1:
        raise RuntimeError("fabric platform plan semantic execute invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    host_changed = repair_host()
    expansion_changed = repair_expansion()
    product_changed = repair_product()
    return host_changed or expansion_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-platform-plan-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
