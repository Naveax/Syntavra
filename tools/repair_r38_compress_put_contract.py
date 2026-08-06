#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESCRIBE = ROOT / "crates" / "syntavra-cli" / "src" / "native_compress_describe.rs"
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
    ),
    (
        "let safe = path.replace(['\\\\', '/'], \"__\");",
        "let safe = path.replace('\\\\', \"__\").replace('/', \"__\");",
    ),
    (
        "let end = if matched.starts_with(['.', '!', '?']) {",
        "let end = if matched.starts_with('.') || matched.starts_with('!') || matched.starts_with('?') {",
    ),
    (
        "let target = target.trim_matches(['{', '}', ':', '(', ')']);",
        "let target = target.trim_matches(|value: char| matches!(value, '{' | '}' | ':' | '(' | ')'));",
    ),
)


def insert_after(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    if source.count(token) == 1:
        return source, False
    if source.count(token) != 0 or source.count(anchor) != 1:
        raise RuntimeError(f"{label} contract is ambiguous")
    return source.replace(anchor, anchor + token, 1), True


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


def normalize_put_source() -> bool:
    source = PUT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for old, new in SOURCE_REPAIRS:
        if old in rendered:
            rendered = rendered.replace(old, new, 1)
            changed = True
        elif new not in rendered:
            raise RuntimeError(f"compress put source normalization anchor missing: {old}")
    if changed:
        PUT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "compress put modules"),
        (SUPPORT, SUPPORT_ANCHOR, "compress put support"),
        (EXECUTE, EXECUTE_ANCHOR, "compress put execute"),
    ):
        rendered, applied = insert_after(rendered, token, anchor, label)
        changed = changed or applied
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
    source_changed = normalize_put_source()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": (
                    describe_changed
                    or source_changed
                    or product_changed
                    or validator_changed
                ),
                "describe_changed": describe_changed,
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
