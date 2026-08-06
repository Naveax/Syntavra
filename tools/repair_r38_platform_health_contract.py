#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "crates" / "syntavra-cli" / "src" / "native_platform_health.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

OLD_GROUP_ROW = '''            Ok(json!({key: value, count_key: amount}))
'''
NEW_GROUP_ROW = '''            let mut object = serde_json::Map::new();
            object.insert(key.to_owned(), Value::String(value));
            object.insert(count_key.to_owned(), json!(amount));
            Ok(Value::Object(object))
'''

MODULE = '''#[path = "native_platform_health.rs"]
mod native_platform_health;
'''
MODULE_ANCHOR = '''#[path = "native_platform_state.rs"]
mod native_platform_state;
'''
SUPPORT = "        || native_platform_health::supports(command)\n"
SUPPORT_ANCHOR = "        || native_adapter_configure::supports(command)\n"
EXECUTE = '''    if native_platform_health::supports(command) {
        let value = native_platform_health::execute(command, project_root, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
EXECUTE_ANCHOR = '''    if native_adapter_configure::supports(command) {
        return native_adapter_configure::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_platform_health_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_adapter_certify_r38.py",\n'


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_health_source() -> bool:
    source = HEALTH.read_text(encoding="utf-8")
    if source.count(NEW_GROUP_ROW) == 1:
        return False
    if source.count(NEW_GROUP_ROW) != 0:
        raise RuntimeError("platform health dynamic group repair count invalid")
    if source.count(OLD_GROUP_ROW) != 1:
        raise RuntimeError("platform health legacy dynamic group row must be unique")
    HEALTH.write_text(
        source.replace(OLD_GROUP_ROW, NEW_GROUP_ROW, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "platform health module"),
        (SUPPORT, SUPPORT_ANCHOR, "platform health support"),
        (EXECUTE, EXECUTE_ANCHOR, "platform health execute"),
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
        raise RuntimeError("platform health validator target count invalid")
    if source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("platform health validator anchor must be unique")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def repair() -> bool:
    values = (repair_health_source(), repair_product(), repair_validator())
    return any(values)


def main() -> int:
    health_changed = repair_health_source()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": health_changed or product_changed or validator_changed,
                "health_changed": health_changed,
                "ok": True,
                "product_changed": product_changed,
                "surface": "native-platform-health",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
