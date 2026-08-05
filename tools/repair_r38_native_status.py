#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "crates" / "syntavra-cli" / "src" / "native_status.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

MODULE_TOKEN = 'mod native_status;'
SUPPORT_TOKEN = '        || native_status::supports(command)\n'
EXECUTE_TOKEN = 'if native_status::supports(command) {'
TEST_TOKEN = 'vec!["status"]'

MODULE_ANCHOR = '''#[path = "native_stats.rs"]
mod native_stats;
'''
MODULE_INSERT = '''#[path = "native_status.rs"]
mod native_status;
'''
SUPPORT_ANCHOR = '        || native_stats::supports(command)\n'
SUPPORT_INSERT = '        || native_status::supports(command)\n'
EXECUTE_ANCHOR = '''    if native_stats::supports(command) {
        return native_stats::execute(project_root, state_root);
    }
'''
EXECUTE_INSERT = '''    if native_status::supports(command) {
        let decision = native_status::execute(arguments, project_root, state_root)?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
'''
TEST_ANCHOR = '            vec!["repair"],\n'
TEST_INSERT = '            vec!["status"],\n'


def insert_once(source: str, *, token: str, anchor: str, addition: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label}: token count must be 0 or 1, got {count}")
    anchor_count = source.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(f"{label}: anchor count must be 1, got {anchor_count}")
    return source.replace(anchor, anchor + addition, 1), True


def repair() -> bool:
    if not STATUS.is_file():
        raise RuntimeError("native status module is missing")
    source = EXPANSION.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, addition, label in (
        (MODULE_TOKEN, MODULE_ANCHOR, MODULE_INSERT, "status module"),
        (SUPPORT_TOKEN, SUPPORT_ANCHOR, SUPPORT_INSERT, "status support"),
        (EXECUTE_TOKEN, EXECUTE_ANCHOR, EXECUTE_INSERT, "status execute"),
        (TEST_TOKEN, TEST_ANCHOR, TEST_INSERT, "status route test"),
    ):
        rendered, applied = insert_once(
            rendered,
            token=token,
            anchor=anchor,
            addition=addition,
            label=label,
        )
        changed = changed or applied
    invalid = {
        token: rendered.count(token)
        for token in (MODULE_TOKEN, SUPPORT_TOKEN, EXECUTE_TOKEN, TEST_TOKEN)
        if rendered.count(token) != 1
    }
    if invalid:
        raise RuntimeError(f"native status wiring invariant failed: {invalid}")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(json.dumps({"changed": changed, "ok": True, "surface": "status"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
