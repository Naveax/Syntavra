#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "crates" / "syntavra-cli" / "src" / "native_hook.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

MODULE_TOKEN = "mod native_hook;"
SUPPORT_TOKEN = "        || native_hook::supports(command)\n"
EXECUTE_TOKEN = "if native_hook::supports(command) {"
TEST_TOKEN = 'vec!["hook"]'

MODULE_ANCHOR = '''#[allow(clippy::pedantic)]
#[path = "native_host.rs"]
mod native_host;
'''
MODULE_INSERT = '''#[path = "native_hook.rs"]
mod native_hook;
'''
SUPPORT_ANCHOR = "        || native_host::supports(command)\n"
SUPPORT_INSERT = "        || native_hook::supports(command)\n"
EXECUTE_ANCHOR = '''    if native_host::supports(command) {
        return native_host::execute(command, arguments, project_root);
    }
'''
EXECUTE_INSERT = '''    if native_hook::supports(command) {
        return native_hook::execute(arguments, project_root, state_root);
    }
'''
TEST_ANCHOR = '            vec!["host", "capabilities"],\n'
TEST_INSERT = '            vec!["hook"],\n'


def add_once(source: str, token: str, anchor: str, addition: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} token count invalid: {count}")
    anchor_count = source.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(f"{label} anchor count invalid: {anchor_count}")
    return source.replace(anchor, anchor + addition, 1), True


def repair() -> bool:
    if not HOOK.is_file():
        raise RuntimeError("native hook module missing")
    source = EXPANSION.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, addition, label in (
        (MODULE_TOKEN, MODULE_ANCHOR, MODULE_INSERT, "hook module"),
        (SUPPORT_TOKEN, SUPPORT_ANCHOR, SUPPORT_INSERT, "hook support"),
        (EXECUTE_TOKEN, EXECUTE_ANCHOR, EXECUTE_INSERT, "hook execute"),
        (TEST_TOKEN, TEST_ANCHOR, TEST_INSERT, "hook route test"),
    ):
        rendered, applied = add_once(rendered, token, anchor, addition, label)
        changed = changed or applied
    invalid = {
        token: rendered.count(token)
        for token in (MODULE_TOKEN, SUPPORT_TOKEN, EXECUTE_TOKEN, TEST_TOKEN)
        if rendered.count(token) != 1
    }
    if invalid:
        raise RuntimeError(f"native hook wiring invariant failed: {invalid}")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(json.dumps({"changed": changed, "ok": True, "surface": "hook"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
