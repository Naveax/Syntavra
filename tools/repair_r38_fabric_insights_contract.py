#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_doctor.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

DOCTOR_REPLACEMENTS = (
    (
        "fn initialize_database(path: &Path) -> Result<Connection, String> {",
        "pub(crate) fn open_database(path: &Path) -> Result<Connection, String> {",
    ),
    (
        "fn integrity(connection: &Connection) -> Result<bool, String> {",
        "pub(crate) fn database_integrity(connection: &Connection) -> Result<bool, String> {",
    ),
    (
        "fn write_output(path: &Path, value: &Value) -> Result<Value, String> {",
        "pub(crate) fn write_json_output(path: &Path, value: &Value) -> Result<Value, String> {",
    ),
    (
        "let database = initialize_database(&database_path)?;",
        "let database = open_database(&database_path)?;",
    ),
    (
        "let analytics_database = integrity(&database)?;",
        "let analytics_database = database_integrity(&database)?;",
    ),
    (
        "|path| write_output(&PathBuf::from(path), &value),",
        "|path| write_json_output(&PathBuf::from(path), &value),",
    ),
)

INSIGHTS_MODULE = '''#[path = "native_fabric_insights.rs"]
mod native_fabric_insights;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
INSIGHTS_SUPPORT = "        || native_fabric_insights::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_route::supports(command)\n"
INSIGHTS_EXECUTE = '''    if native_fabric_insights::supports(command) {
        return native_fabric_insights::execute(&arguments, state_root).map(Some);
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


def repair_doctor_api() -> bool:
    source = DOCTOR.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for old, new in DOCTOR_REPLACEMENTS:
        if new in rendered:
            if old in rendered:
                raise RuntimeError(f"legacy doctor API token remains beside canonical token: {old}")
            continue
        if rendered.count(old) != 1:
            raise RuntimeError(f"doctor API contract token must be unique: {old}")
        rendered = rendered.replace(old, new, 1)
        changed = True
    for token in (
        "pub(crate) fn open_database",
        "pub(crate) fn database_integrity",
        "pub(crate) fn write_json_output",
    ):
        if rendered.count(token) != 1:
            raise RuntimeError(f"doctor shared API invariant failed: {token}")
    if changed:
        DOCTOR.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (INSIGHTS_MODULE, MODULE_ANCHOR, "fabric insights module"),
        (INSIGHTS_SUPPORT, SUPPORT_ANCHOR, "fabric insights support"),
        (INSIGHTS_EXECUTE, EXECUTE_ANCHOR, "fabric insights execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if any(
        rendered.count(token) != 1
        for token in (INSIGHTS_MODULE, INSIGHTS_SUPPORT, INSIGHTS_EXECUTE)
    ):
        raise RuntimeError("fabric insights product wiring invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    doctor_changed = repair_doctor_api()
    product_changed = repair_product()
    return doctor_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-insights-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
