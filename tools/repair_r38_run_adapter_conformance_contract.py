#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_STATE = ROOT / "crates" / "syntavra-cli" / "src" / "native_platform_state.rs"
ADAPTERS = ROOT / "crates" / "syntavra-cli" / "src" / "native_run_adapters.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

DIRECTORIES = '''    fs::create_dir_all(root.join("adapter-receipts"))
        .map_err(|error| format!("PLATFORM_STATE_ADAPTER_RECEIPTS_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(root.join("adapter-backups"))
        .map_err(|error| format!("PLATFORM_STATE_ADAPTER_BACKUPS_CREATE_FAILED:{error}"))?;
'''
DIRECTORY_ANCHOR = "    artifacts(&root)?;\n"

SHARED_API = '''pub(crate) fn catalog_value() -> Result<Value, String> {
    serde_json::from_str::<Value>(CATALOG)
        .map_err(|error| format!("ADAPTER_CATALOG_INVALID:{error}"))
}

pub(crate) fn contract(adapter_id: &str) -> Result<Value, String> {
    let catalog = catalog_value()?;
    catalog["records"]
        .as_array()
        .and_then(|records| {
            records
                .iter()
                .find(|record| record["adapter_id"].as_str() == Some(adapter_id))
        })
        .cloned()
        .ok_or_else(|| format!("ADAPTER_CONTRACT_NOT_FOUND:{adapter_id}"))
}

pub(crate) fn detection(adapter_id: &str, project_root: &Path) -> Result<Value, String> {
    let record = contract(adapter_id)?;
    let commands = record["detection_commands"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|command| find_command(command).is_some())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let paths = record["config_paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(|candidate| config_path(candidate, project_root))
        .filter(|path| path.exists())
        .map(|path| path.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    Ok(json!({
        "adapter_id": adapter_id,
        "detected": !commands.is_empty() || !paths.is_empty(),
        "commands": commands,
        "paths": paths,
        "surface": record["surface"],
        "integration_modes": record["integration_modes"],
    }))
}

'''
API_SIGNATURE = "pub(crate) fn catalog_value() -> Result<Value, String> {\n"
API_ANCHOR = "pub fn execute(\n"
OLD_CATALOG_PARSE = '''    let catalog = serde_json::from_str::<Value>(CATALOG)
        .map_err(|error| format!("ADAPTER_CATALOG_INVALID:{error}"))?;
'''
NEW_CATALOG_PARSE = '''    let catalog = catalog_value()?;
'''

MODULE = '''#[path = "native_run_adapter_conformance.rs"]
mod native_run_adapter_conformance;
'''
MODULE_ANCHOR = '''#[path = "native_run_adapters.rs"]
mod native_run_adapters;
'''
SUPPORT = "        || native_run_adapter_conformance::supports(command)\n"
SUPPORT_ANCHOR = "        || native_run_adapters::supports(command)\n"
EXECUTE = '''    if native_run_adapter_conformance::supports(command) {
        return native_run_adapter_conformance::execute(&arguments, project_root, state_root)
            .map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_run_adapters::supports(command) {
        return native_run_adapters::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_adapter_conformance_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_adapters_r38.py",\n'


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_platform_state() -> bool:
    source = PLATFORM_STATE.read_text(encoding="utf-8")
    if source.count(DIRECTORIES) == 1:
        return False
    if source.count(DIRECTORIES) != 0:
        raise RuntimeError("adapter runtime directory contract count invalid")
    if source.count(DIRECTORY_ANCHOR) != 1:
        raise RuntimeError("adapter runtime directory anchor must be unique")
    PLATFORM_STATE.write_text(
        source.replace(DIRECTORY_ANCHOR, DIRECTORIES + DIRECTORY_ANCHOR, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_adapter_api() -> bool:
    source = ADAPTERS.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if rendered.count(API_SIGNATURE) == 0:
        if rendered.count(API_ANCHOR) != 1:
            raise RuntimeError("adapter shared API anchor must be unique")
        rendered = rendered.replace(API_ANCHOR, SHARED_API + API_ANCHOR, 1)
        changed = True
    elif rendered.count(API_SIGNATURE) != 1:
        raise RuntimeError("adapter shared API signature count invalid")

    if rendered.count(API_ANCHOR) != 1:
        raise RuntimeError("adapter execute anchor must be unique")
    execute_source = rendered[rendered.index(API_ANCHOR) :]
    if OLD_CATALOG_PARSE in execute_source:
        if execute_source.count(OLD_CATALOG_PARSE) != 1:
            raise RuntimeError("legacy adapter catalog parse must be unique in execute")
        execute_source = execute_source.replace(OLD_CATALOG_PARSE, NEW_CATALOG_PARSE, 1)
        rendered = rendered[: rendered.index(API_ANCHOR)] + execute_source
        changed = True
    elif NEW_CATALOG_PARSE not in execute_source:
        raise RuntimeError("canonical adapter catalog parse missing from execute")

    if changed:
        ADAPTERS.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "adapter conformance module"),
        (SUPPORT, SUPPORT_ANCHOR, "adapter conformance support"),
        (EXECUTE, EXECUTE_ANCHOR, "adapter conformance execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0:
        raise RuntimeError("adapter conformance validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("adapter conformance validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    values = (
        repair_platform_state(),
        repair_adapter_api(),
        repair_product(),
        repair_validator(),
    )
    return any(values)


def main() -> int:
    platform_changed = repair_platform_state()
    api_changed = repair_adapter_api()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(json.dumps({
        "api_changed": api_changed,
        "changed": platform_changed or api_changed or product_changed or validator_changed,
        "ok": True,
        "platform_changed": platform_changed,
        "product_changed": product_changed,
        "surface": "native-run-adapter-conformance",
        "validator_changed": validator_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
