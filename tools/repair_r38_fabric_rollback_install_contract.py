#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_install.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

PUBLIC_FUNCTIONS = (
    "now",
    "safe_target",
    "digest",
    "remove_target",
    "copy_tree",
    "initialize_database",
)
MODULE = '''#[path = "native_fabric_rollback_install.rs"]
mod native_fabric_rollback_install;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
SUPPORT = "        || native_fabric_rollback_install::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_route::supports(command)\n"
EXECUTE = '''    if native_fabric_rollback_install::supports(command) {
        return native_fabric_rollback_install::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_fabric_route::supports(command) {
        return native_fabric_route::execute(&arguments, state_root).map(Some);
    }
'''


def expose_install_helpers() -> bool:
    source = INSTALL.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for name in PUBLIC_FUNCTIONS:
        public = f"pub(crate) fn {name}("
        private = f"fn {name}("
        if rendered.count(public) == 1:
            continue
        if rendered.count(public) != 0:
            raise RuntimeError(f"public install helper {name} must be unique")
        private_lines = [line for line in rendered.splitlines() if line.startswith(private)]
        if len(private_lines) != 1:
            raise RuntimeError(f"private install helper {name} must be unique")
        rendered = rendered.replace(private_lines[0], private_lines[0].replace(private, public, 1), 1)
        changed = True
    if changed:
        INSTALL.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def insert_once(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def wire_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "rollback module"),
        (SUPPORT, SUPPORT_ANCHOR, "rollback support"),
        (EXECUTE, EXECUTE_ANCHOR, "rollback execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    helpers_changed = expose_install_helpers()
    wiring_changed = wire_product()
    return helpers_changed or wiring_changed


def main() -> int:
    helpers_changed = expose_install_helpers()
    wiring_changed = wire_product()
    print(json.dumps({
        "changed": helpers_changed or wiring_changed,
        "helpers_changed": helpers_changed,
        "ok": True,
        "surface": "native-fabric-rollback-install",
        "wiring_changed": wiring_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
