#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
STORE = ROOT / "crates" / "syntavra-cli" / "src" / "native_artifact_store.rs"
ROUTE = ROOT / "crates" / "syntavra-cli" / "src" / "native_run_artifact_query.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

ROUTE_MODULE = '''#[path = "native_run_artifact_query.rs"]
mod native_run_artifact_query;
'''
MODULE_ANCHOR = '''#[path = "native_run_artifact_put.rs"]
mod native_run_artifact_put;
'''
SUPPORT = "        || native_run_artifact_query::supports(command)\n"
SUPPORT_ANCHOR = "        || native_run_artifact_put::supports(command)\n"
EXECUTE = '''    if native_run_artifact_query::supports(command) {
        return native_run_artifact_query::execute(&arguments, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_run_artifact_put::supports(command) {
        return native_run_artifact_put::execute(&arguments, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_artifact_query_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_artifact_put_r38.py",\n'


def validate_sources() -> None:
    required = {
        STORE: (
            "pub(crate) struct NativeArtifactStore",
            "pub(crate) fn record(",
            "pub(crate) fn read(",
            "ARTIFACT_INTEGRITY_FAILURE",
        ),
        ROUTE: (
            'matches!(command, [root, action] if root == "run" && action == "artifact-query")',
            "fn splitlines_python",
            "fn select_json",
            "fn select_lines",
            "fn redact",
            "fn estimate_tokens",
            "NativeArtifactStore::open",
        ),
    }
    for path, markers in required.items():
        if not path.is_file():
            raise RuntimeError(f"native artifact query source is missing: {path}")
        source = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in source:
                raise RuntimeError(
                    f"native artifact query source contract is missing: {path.name}:{marker}"
                )


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    if "mod native_run_artifact_query;" not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("artifact query module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + ROUTE_MODULE, 1)
        changed = True

    if "|| native_run_artifact_query::supports(command)" not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("artifact query support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    support_marker = "if native_run_artifact_query::supports(command) {"
    execute_marker = "native_run_artifact_query::execute("
    presence = (support_marker in rendered, execute_marker in rendered)
    if presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("artifact query execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif presence != (True, True):
        raise RuntimeError("artifact query execute wiring is partial")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("artifact query validator contract is ambiguous")
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
                "surface": "native-run-artifact-query",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
