#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_install.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

MODULE = '''#[path = "native_fabric_verify_install.rs"]
mod native_fabric_verify_install;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_route.rs"]
mod native_fabric_route;
'''
SUPPORT = "        || native_fabric_verify_install::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_route::supports(command)\n"
EXECUTE = '''    if native_fabric_verify_install::supports(command) {
        let decision = native_fabric_verify_install::execute(
            &arguments,
            project_root,
            state_root,
        )?;
        if decision.exit_code != 0 {
            emit_failed_decision(&decision.value, decision.exit_code);
        }
        return Ok(Some(decision.value));
    }
'''
EXECUTE_ANCHOR = '''    if native_fabric_route::supports(command) {
        return native_fabric_route::execute(&arguments, state_root).map(Some);
    }
'''


def expose_verify() -> bool:
    source = INSTALL.read_text(encoding="utf-8")
    public = "pub(crate) fn verify("
    private = "fn verify("
    if any(line.startswith(public) for line in source.splitlines()):
        return False
    private_lines = [line for line in source.splitlines() if line.startswith(private)]
    if len(private_lines) != 1:
        raise RuntimeError("fabric install verify helper must be unique")
    INSTALL.write_text(
        source.replace(
            private_lines[0],
            private_lines[0].replace(private, public, 1),
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return True


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
        (MODULE, MODULE_ANCHOR, "verify module"),
        (SUPPORT, SUPPORT_ANCHOR, "verify support"),
        (EXECUTE, EXECUTE_ANCHOR, "verify execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    return expose_verify() or wire_product()


def main() -> int:
    helper_changed = expose_verify()
    wiring_changed = wire_product()
    print(json.dumps({
        "changed": helper_changed or wiring_changed,
        "helper_changed": helper_changed,
        "ok": True,
        "surface": "native-fabric-verify-install",
        "wiring_changed": wiring_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
