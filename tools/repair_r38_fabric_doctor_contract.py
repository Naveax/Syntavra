#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "crates" / "syntavra-cli" / "src" / "native_host.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"
DOCTOR = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_doctor.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

DOCTOR_CONTRACT = '''
pub(crate) fn doctor_contract(host: &str) -> Value {
    let active = host_spec(host);
    let specs = host_specs();
    let negotiation = negotiate_value(host, true, None);
    json!({
        "known_host": specs.iter().any(|spec| spec.host == host),
        "mcp_available": active.supports_mcp,
        "result_replacement": active.supports_result_replacement,
        "enforced_mode": negotiation
            .get("enforced")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        "platform_registry_size": specs.len(),
        "negotiation": negotiation,
    })
}

'''
HOST_ANCHOR = "fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {\n"

EXPANSION_BRIDGE = '''pub(crate) fn doctor_host_contract(host: &str) -> Value {
    native_host::doctor_contract(host)
}

'''
EXPANSION_ANCHOR = "pub fn supports(command: &[String]) -> bool {\n"
OLD_DOCTOR_CALL = "super::native_host::doctor_contract(&host)"
NEW_DOCTOR_CALL = "super::native_expansion::doctor_host_contract(&host)"

DOCTOR_MODULE = '''#[path = "native_fabric_doctor.rs"]
mod native_fabric_doctor;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
DOCTOR_SUPPORT = "        || native_fabric_doctor::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_route::supports(command)\n"
DOCTOR_EXECUTE = '''    if native_fabric_doctor::supports(command) {
        return native_fabric_doctor::execute(&arguments, project_root, state_root).map(Some);
    }
'''
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
        DOCTOR_CONTRACT,
        HOST_ANCHOR,
        "native host doctor contract",
    )
    if rendered.count("pub(crate) fn doctor_contract") != 1:
        raise RuntimeError("native host doctor contract invariant failed")
    if changed:
        HOST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered, changed = insert_once(
        source,
        EXPANSION_BRIDGE,
        EXPANSION_ANCHOR,
        "native expansion doctor host bridge",
    )
    if rendered.count("pub(crate) fn doctor_host_contract") != 1:
        raise RuntimeError("native expansion doctor bridge invariant failed")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_doctor_call() -> bool:
    source = DOCTOR.read_text(encoding="utf-8")
    if NEW_DOCTOR_CALL in source:
        if OLD_DOCTOR_CALL in source:
            raise RuntimeError("legacy direct native_host doctor call remains")
        return False
    if source.count(OLD_DOCTOR_CALL) != 1:
        raise RuntimeError("fabric doctor host call contract not found")
    DOCTOR.write_text(
        source.replace(OLD_DOCTOR_CALL, NEW_DOCTOR_CALL, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (DOCTOR_MODULE, MODULE_ANCHOR, "fabric doctor module"),
        (DOCTOR_SUPPORT, SUPPORT_ANCHOR, "fabric doctor support"),
        (DOCTOR_EXECUTE, EXECUTE_ANCHOR, "fabric doctor execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if any(
        rendered.count(token) != 1
        for token in (DOCTOR_MODULE, DOCTOR_SUPPORT, DOCTOR_EXECUTE)
    ):
        raise RuntimeError("fabric doctor product wiring invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    host_changed = repair_host()
    expansion_changed = repair_expansion()
    doctor_changed = repair_doctor_call()
    product_changed = repair_product()
    return host_changed or expansion_changed or doctor_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-doctor-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
