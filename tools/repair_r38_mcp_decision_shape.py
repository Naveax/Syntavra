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
VERSION_REMOVE = '    value.remove("version");\n'
CHANNEL_REMOVE = '    value.remove("channel");\n'


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    version_count = source.count(VERSION_REMOVE)
    channel_count = source.count(CHANNEL_REMOVE)
    if version_count == 1 and channel_count == 1:
        return False
    if version_count != 0 or channel_count != 0:
        raise RuntimeError(
            "native MCP decision shape is partially canonical: "
            f"version_remove={version_count}, channel_remove={channel_count}"
        )

    legacy_count = source.count(LEGACY)
    if legacy_count != 1:
        raise RuntimeError(
            "native MCP decision legacy block must be unique before repair: "
            f"legacy={legacy_count}"
        )
    rendered = source.replace(LEGACY, CANONICAL, 1)
    if rendered.count(VERSION_REMOVE) != 1 or rendered.count(CHANNEL_REMOVE) != 1:
        raise RuntimeError("native MCP decision semantic insertion invariant failed")
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return True


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
