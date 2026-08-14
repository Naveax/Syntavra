#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from repair_r38_fabric_installations_behavior import repair as repair_behavior

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

MODULE = '''#[path = "native_fabric_installations.rs"]
mod native_fabric_installations;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_install.rs"]
mod native_fabric_install;
'''
SUPPORT = "        || native_fabric_installations::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_install::supports(command)\n"
EXECUTE = '''    if native_fabric_installations::supports(command) {
        return native_fabric_installations::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_SIGNATURE = "native_fabric_installations::execute(&arguments"
EXECUTE_ANCHOR = '''    if native_fabric_install::supports(command) {
        return native_fabric_install::execute(&arguments, project_root, state_root).map(Some);
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


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "fabric installations module"),
        (SUPPORT, SUPPORT_ANCHOR, "fabric installations support"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied

    execute_count = rendered.count(EXECUTE_SIGNATURE)
    if execute_count == 0:
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("fabric installations execute anchor must be unique")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE + EXECUTE_ANCHOR, 1)
        changed = True
    elif execute_count != 1:
        raise RuntimeError(f"fabric installations execute count invalid: {execute_count}")

    if rendered.count(MODULE) != 1 or rendered.count(SUPPORT) != 1:
        raise RuntimeError("fabric installations wiring invariant failed")
    if rendered.count(EXECUTE_SIGNATURE) != 1:
        raise RuntimeError("fabric installations semantic execute invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    behavior_changed = repair_behavior()
    product_changed = repair_product()
    return behavior_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-installations-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
