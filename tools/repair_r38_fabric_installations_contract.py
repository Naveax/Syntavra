#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

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


def repair() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "fabric installations module"),
        (SUPPORT, SUPPORT_ANCHOR, "fabric installations support"),
        (EXECUTE, EXECUTE_ANCHOR, "fabric installations execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if any(rendered.count(token) != 1 for token in (MODULE, SUPPORT, EXECUTE)):
        raise RuntimeError("fabric installations wiring invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


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
