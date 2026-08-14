#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_mcp.rs"

LEGACY = (
    '        return "Expecting property name enclosed in double quotes: '
    'line 1 column 2 (char 1)"\n'
    '            .to_owned();\n'
)
CANONICAL = (
    '        return "Expecting property name enclosed in double quotes: '
    'line 2 column 1 (char 2)"\n'
    '            .to_owned();\n'
)
CANONICAL_TOKEN = "line 2 column 1 (char 2)"
LEGACY_TOKEN = "line 1 column 2 (char 1)"


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    canonical_count = source.count(CANONICAL_TOKEN)
    legacy_count = source.count(LEGACY_TOKEN)
    if canonical_count == 1 and legacy_count == 0:
        return False
    if canonical_count != 0 or legacy_count != 1:
        raise RuntimeError(
            "native MCP parse-error contract state invalid: "
            f"legacy={legacy_count}, canonical={canonical_count}"
        )
    if source.count(LEGACY) != 1:
        raise RuntimeError("native MCP parse-error legacy block must be unique")
    rendered = source.replace(LEGACY, CANONICAL, 1)
    if rendered.count(CANONICAL_TOKEN) != 1 or LEGACY_TOKEN in rendered:
        raise RuntimeError("native MCP parse-error semantic insertion failed")
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-mcp-parse-error",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
