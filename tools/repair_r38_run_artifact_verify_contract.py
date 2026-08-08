#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
ROUTE = ROOT / "crates" / "syntavra-cli" / "src" / "native_run_artifact_verify.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

ROUTE_MODULE = '''#[path = "native_run_artifact_verify.rs"]
mod native_run_artifact_verify;
'''
MODULE_ANCHOR = '''#[path = "native_run_artifact_stats.rs"]
mod native_run_artifact_stats;
'''
SUPPORT = "        || native_run_artifact_verify::supports(command)\n"
SUPPORT_ANCHOR = "        || native_run_artifact_stats::supports(command)\n"
EXECUTE = '''    if native_run_artifact_verify::supports(command) {
        let value = native_run_artifact_verify::execute(&arguments, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
EXECUTE_ANCHOR = '''    if native_run_artifact_stats::supports(command) {
        return native_run_artifact_stats::execute(&arguments, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_run_artifact_verify_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_run_artifact_stats_r38.py",\n'


def validate_sources() -> None:
    required = {
        ROUTE: (
            'matches!(command, [root, action] if root == "run" && action == "artifact-verify")',
            "fn parse_artifact_id",
            "NativeArtifactStore::open",
            'format!("missing:{artifact_id}")',
            'format!("hash:{artifact_id}")',
            "Sha256::digest",
        ),
    }
    for path, markers in required.items():
        if not path.is_file():
            raise RuntimeError(f"native artifact verify source is missing: {path}")
        source = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in source:
                raise RuntimeError(
                    f"native artifact verify source contract is missing: {path.name}:{marker}"
                )


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    if "mod native_run_artifact_verify;" not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("artifact verify module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + ROUTE_MODULE, 1)
        changed = True

    if "|| native_run_artifact_verify::supports(command)" not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("artifact verify support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    support_marker = "if native_run_artifact_verify::supports(command) {"
    execute_marker = "native_run_artifact_verify::execute("
    presence = (support_marker in rendered, execute_marker in rendered)
    if presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("artifact verify execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif presence != (True, True):
        raise RuntimeError("artifact verify execute wiring is partial")

    if support_marker in rendered:
        verify_block_start = rendered.index(support_marker)
        verify_block_end = rendered.find("    if ", verify_block_start + len(support_marker))
        verify_block = rendered[verify_block_start:] if verify_block_end == -1 else rendered[verify_block_start:verify_block_end]
        required_wiring = (
            'value["ok"].as_bool() == Some(false)',
            "emit_failed_decision(&value, 3);",
        )
        for marker in required_wiring:
            if marker not in verify_block:
                raise RuntimeError(f"artifact verify failure-exit wiring is missing: {marker}")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("artifact verify validator contract is ambiguous")
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
                "surface": "native-run-artifact-verify",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
