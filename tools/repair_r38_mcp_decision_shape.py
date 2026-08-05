#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_mcp.rs"

LEGACY = '''    let mut value = body
        .as_object()
        .cloned()
        .ok_or_else(|| "MCP_DECISION_OBJECT_FAILED".to_owned())?;
    value.insert(
        "receipt_hash".to_owned(),
        Value::String(hash_json(&body)?),
    );
'''
CANONICAL = '''    let mut value = body
        .as_object()
        .cloned()
        .ok_or_else(|| "MCP_DECISION_OBJECT_FAILED".to_owned())?;
    value.remove("version");
    value.remove("channel");
    value.insert(
        "receipt_hash".to_owned(),
        Value::String(hash_json(&body)?),
    );
'''


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    legacy_count = source.count(LEGACY)
    canonical_count = source.count(CANONICAL)
    if legacy_count == 1 and canonical_count == 0:
        TARGET.write_text(
            source.replace(LEGACY, CANONICAL, 1),
            encoding="utf-8",
            newline="\n",
        )
        return True
    if legacy_count == 0 and canonical_count == 1:
        return False
    raise RuntimeError(
        "native MCP decision shape is neither one legacy nor one canonical block: "
        f"legacy={legacy_count}, canonical={canonical_count}"
    )


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-mcp-decision-shape",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
