#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESCRIBE = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_describe.rs"
EVIDENCE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_store.rs"
PUT = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_put.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_evidence_store.rs"]
mod native_evidence_store;
#[path = "native_compress_put.rs"]
mod native_compress_put;
'''
MODULE_ANCHOR = '''#[path = "native_compress_describe.rs"]
mod native_compress_describe;
'''
SUPPORT = "        || native_compress_put::supports(command)\n"
SUPPORT_ANCHOR = "        || native_compress_describe::supports(command)\n"
EXECUTE = '''    if native_compress_put::supports(command) {
        return native_compress_put::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_compress_describe::supports(command) {
        return native_compress_describe::execute(&arguments, state_root).map(Some);
    }
'''
TARGET = '    "tests/runtime/test_native_compress_put_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_compress_describe_r38.py",\n'

SOURCE_REPAIRS = (
    (
        "|| !text.ends_with(['\\n', '\\r'])",
        "|| !(text.ends_with('\\n') || text.ends_with('\\r'))",
        ("text.ends_with('\\n')", "text.ends_with('\\r')"),
    ),
    (
        "let safe = path.replace(['\\\\', '/'], \"__\");",
        "let safe = path.replace('\\\\', \"__\").replace('/', \"__\");",
        ("path.replace('\\\\', \"__\")", ".replace('/', \"__\")"),
    ),
    (
        "let end = if matched.starts_with(['.', '!', '?']) {",
        "let end = if matched.starts_with('.') || matched.starts_with('!') || matched.starts_with('?') {",
        (
            "matched.starts_with('.')",
            "matched.starts_with('!')",
            "matched.starts_with('?')",
        ),
    ),
    (
        "let target = target.trim_matches(['{', '}', ':', '(', ')']);",
        "let target = target.trim_matches(|value: char| matches!(value, '{' | '}' | ':' | '(' | ')'));",
        ("trim_matches(|value: char|", "matches!(value"),
    ),
    (
        "use std::io::{Read as _, Write as _};",
        "use std::io::Read as _;",
        ("use std::io::Read as _;",),
    ),
)


def expose_describe_helpers() -> bool:
    source = DESCRIBE.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for name in ("initialize_database", "describe"):
        public = re.compile(rf"(?m)^pub\(crate\) fn {name}\s*\(")
        private = re.compile(rf"(?m)^fn {name}\s*\(")
        if public.search(rendered):
            continue
        matches = list(private.finditer(rendered))
        if len(matches) != 1:
            raise RuntimeError(f"compress describe helper exposure is ambiguous: {name}")
        rendered = private.sub(f"pub(crate) fn {name}(", rendered, count=1)
        changed = True
    if changed:
        DESCRIBE.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def suppress_evidence_dead_code() -> bool:
    source = EVIDENCE.read_text(encoding="utf-8")
    token = "#![allow(dead_code)]\n"
    anchor = "#![forbid(unsafe_code)]\n"
    if source.count(token) == 1:
        return False
    if source.count(token) != 0 or source.count(anchor) != 1:
        raise RuntimeError("native evidence warning guard is ambiguous")
    EVIDENCE.write_text(
        source.replace(anchor, anchor + token, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def normalize_put_source() -> bool:
    source = PUT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for old, new, markers in SOURCE_REPAIRS:
        if old in rendered:
            rendered = rendered.replace(old, new, 1)
            changed = True
        elif not all(marker in rendered for marker in markers):
            raise RuntimeError(f"compress put source normalization anchor missing: {old}")
    if changed:
        PUT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False

    evidence_module = "mod native_evidence_store;"
    put_module = "mod native_compress_put;"
    module_presence = (evidence_module in rendered, put_module in rendered)
    if module_presence == (False, False):
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("compress put module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + MODULE, 1)
        changed = True
    elif module_presence != (True, True):
        raise RuntimeError("compress put module wiring is partial")

    support_marker = "|| native_compress_put::supports(command)"
    if support_marker not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("compress put support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True

    execute_support = "if native_compress_put::supports(command) {"
    execute_call = "native_compress_put::execute("
    execute_presence = (execute_support in rendered, execute_call in rendered)
    if execute_presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("compress put execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif execute_presence != (True, True):
        raise RuntimeError("compress put execute wiring is partial")

    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("compress put validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    describe_changed = expose_describe_helpers()
    evidence_changed = suppress_evidence_dead_code()
    source_changed = normalize_put_source()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": (
                    describe_changed
                    or evidence_changed
                    or source_changed
                    or product_changed
                    or validator_changed
                ),
                "describe_changed": describe_changed,
                "evidence_changed": evidence_changed,
                "ok": True,
                "product_changed": product_changed,
                "source_changed": source_changed,
                "surface": "native-compress-put",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
