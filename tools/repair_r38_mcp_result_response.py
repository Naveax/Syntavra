#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_mcp.rs"

LEGACY_SIGNATURE = '''fn result_response(
    request_id: Value,
    tool: &str,
    value: Value,
'''
CANONICAL_SIGNATURE = '''fn result_response(
    request_id: Value,
    value: Value,
'''
LEGACY_CALL = '''result_response(request_id, tool, value, &decision, &active_manifest)'''
CANONICAL_CALL = '''result_response(request_id, value, &decision, &active_manifest)'''


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    signature_state = (source.count(LEGACY_SIGNATURE), source.count(CANONICAL_SIGNATURE))
    call_state = (source.count(LEGACY_CALL), source.count(CANONICAL_CALL))
    if signature_state == (0, 1) and call_state == (0, 1):
        return False
    if signature_state != (1, 0) or call_state != (1, 0):
        raise RuntimeError(
            "native MCP result-response state invalid: "
            f"signature={signature_state}, call={call_state}"
        )
    rendered = source.replace(LEGACY_SIGNATURE, CANONICAL_SIGNATURE, 1)
    rendered = rendered.replace(LEGACY_CALL, CANONICAL_CALL, 1)
    if rendered.count(CANONICAL_SIGNATURE) != 1 or rendered.count(CANONICAL_CALL) != 1:
        raise RuntimeError("native MCP result-response semantic insertion failed")
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-mcp-result-response",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
