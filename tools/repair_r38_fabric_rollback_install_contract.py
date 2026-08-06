#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_install.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
TEST = ROOT / "tests" / "runtime" / "test_native_fabric_rollback_install_r38.py"

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
OLD_NEGATIVE = '''        database = project / "state" / "host-installations.sqlite3"
        assert database.is_file()
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_install_transactions"
            ).fetchone()[0] == 0
'''
NEW_NEGATIVE = '''        database = project / "state" / "host-installations.sqlite3"
        assert not database.exists()
'''


def expose_install_helpers() -> bool:
    source = INSTALL.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for name in PUBLIC_FUNCTIONS:
        public = f"pub(crate) fn {name}("
        private = f"fn {name}("
        public_lines = [line for line in rendered.splitlines() if line.startswith(public)]
        if len(public_lines) == 1:
            continue
        if public_lines:
            raise RuntimeError(f"public install helper {name} must be unique")
        private_lines = [line for line in rendered.splitlines() if line.startswith(private)]
        if len(private_lines) != 1:
            raise RuntimeError(f"private install helper {name} must be unique")
        rendered = rendered.replace(
            private_lines[0],
            private_lines[0].replace(private, public, 1),
            1,
        )
        changed = True
    if changed:
        INSTALL.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    if NEW_NEGATIVE in source:
        if OLD_NEGATIVE in source:
            raise RuntimeError("legacy rollback missing-ledger assertion remains")
        return False
    if source.count(OLD_NEGATIVE) != 1:
        raise RuntimeError("rollback missing-ledger assertion contract not found")
    TEST.write_text(
        source.replace(OLD_NEGATIVE, NEW_NEGATIVE, 1),
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
    test_changed = repair_test()
    wiring_changed = wire_product()
    return helpers_changed or test_changed or wiring_changed


def main() -> int:
    helpers_changed = expose_install_helpers()
    test_changed = repair_test()
    wiring_changed = wire_product()
    print(json.dumps({
        "changed": helpers_changed or test_changed or wiring_changed,
        "helpers_changed": helpers_changed,
        "ok": True,
        "surface": "native-fabric-rollback-install",
        "test_changed": test_changed,
        "wiring_changed": wiring_changed,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
