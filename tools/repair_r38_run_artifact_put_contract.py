#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
STORE = ROOT / "crates" / "syntavra-cli" / "src" / "native_artifact_store.rs"
ROUTE = ROOT / "crates" / "syntavra-cli" / "src" / "native_run_artifact_put.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

STORE_MODULE = '''#[path = "native_artifact_store.rs"]
mod native_artifact_store;
'''
ROUTE_MODULE = '''#[path = "native_run_artifact_put.rs"]
mod native_run_artifact_put;
'''
MODULE_ANCHOR = '''#[path = "native_audit_config.rs"]
mod native_audit_config;
'''
SUPPORT = "        || native_run_artifact_put::supports(command)\n"
SUPPORT_ANCHOR = "        || native_evidence_get::supports(command)\n"
EXECUTE = '''    if native_run_artifact_put::supports(command) {
        return native_run_artifact_put::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_evidence_get::supports(command) {
        return native_evidence_get::execute(&arguments, project_root, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_artifact_put_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_evidence_rotate_key_r38.py",\n'


def validate_sources() -> None:
    required = {
        STORE: (
            "pub(crate) struct NativeArtifactStore",
            "pub(crate) fn put(",
            "INSERT OR IGNORE INTO artifacts",
            "PRAGMA journal_mode=WAL",
            "fn write_object_once",
        ),
        ROUTE: (
            'action == "artifact-put"',
            "fn load_input",
            "normalize_universal_newlines",
            "NativeArtifactStore::open",
        ),
    }
    for path, markers in required.items():
        if not path.is_file():
            raise RuntimeError(f"native artifact source is missing: {path}")
        source = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in source:
                raise RuntimeError(
                    f"native artifact source contract is missing: {path.name}:{marker}"
                )


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    store_present = "mod native_artifact_store;" in rendered
    route_present = "mod native_run_artifact_put;" in rendered
    if not store_present or not route_present:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("artifact module anchor is ambiguous")
        modules = ""
        if not store_present:
            modules += STORE_MODULE
        if not route_present:
            modules += ROUTE_MODULE
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + modules, 1)
        changed = True

    if "|| native_run_artifact_put::supports(command)" not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("artifact put support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    support_marker = "if native_run_artifact_put::supports(command) {"
    execute_marker = "native_run_artifact_put::execute("
    presence = (support_marker in rendered, execute_marker in rendered)
    if presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("artifact put execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif presence != (True, True):
        raise RuntimeError("artifact put execute wiring is partial")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("artifact put validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    validate_sources()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": product_changed or validator_changed,
                "ok": True,
                "product_changed": product_changed,
                "source_changed": False,
                "surface": "native-run-artifact-put",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
