#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from repair_r38_mcp_decision_shape import repair as repair_decision_shape
from repair_r38_mcp_parse_error import repair as repair_parse_error
from repair_r38_mcp_result_response import repair as repair_result_response

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

MODULE = '''#[path = "native_mcp.rs"]
mod native_mcp;
'''
MODULE_ANCHOR = '''#[path = "native_memory.rs"]
mod native_memory;
'''
SUPPORT = "        || native_mcp::supports(command)\n"
SUPPORT_ANCHOR = "        || native_memory::supports(command)\n"
EXECUTE = '''    if native_mcp::supports(command) {
        native_mcp::serve(arguments, project_root, state_root);
    }
'''
EXECUTE_ANCHOR = '''    if native_memory::supports(command) {
        return native_memory::execute(command, arguments, project_root, state_root);
    }
'''
TEST_ROUTE = '            vec!["mcp"],\n'
TEST_ANCHOR = '            vec!["hook"],\n'


def insert_once(source: str, token: str, anchor: str, *, after: bool, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    replacement = anchor + token if after else token + anchor
    return source.replace(anchor, replacement, 1), True


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, after, label in (
        (MODULE, MODULE_ANCHOR, True, "MCP module"),
        (SUPPORT, SUPPORT_ANCHOR, True, "MCP support"),
        (EXECUTE, EXECUTE_ANCHOR, True, "MCP execute"),
        (TEST_ROUTE, TEST_ANCHOR, True, "MCP route test"),
    ):
        rendered, applied = insert_once(
            rendered,
            token,
            anchor,
            after=after,
            label=label,
        )
        changed = changed or applied

    invariants = {
        "module": rendered.count(MODULE),
        "support": rendered.count(SUPPORT),
        "execute": rendered.count(EXECUTE),
        "test": rendered.count(TEST_ROUTE),
    }
    if invariants != {"module": 1, "support": 1, "execute": 1, "test": 1}:
        raise RuntimeError(f"MCP native wiring invariant failed: {invariants}")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    wiring_changed = repair()
    decision_changed = repair_decision_shape()
    parse_error_changed = repair_parse_error()
    result_response_changed = repair_result_response()
    print(
        json.dumps(
            {
                "changed": (
                    wiring_changed
                    or decision_changed
                    or parse_error_changed
                    or result_response_changed
                ),
                "decision_shape_changed": decision_changed,
                "ok": True,
                "parse_error_changed": parse_error_changed,
                "result_response_changed": result_response_changed,
                "surface": "native-mcp",
                "wiring_changed": wiring_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
