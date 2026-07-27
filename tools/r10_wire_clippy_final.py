#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import r10_wire_source_final

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} marker, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    result = r10_wire_source_final.main()

    path = ROOT / "crates" / "syntavra-cli" / "src" / "broker_live_snapshot_contract.rs"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "const RETRY_SLEEP_MILLISECONDS: u64 = 10;\n",
        "const RETRY_SLEEP_MILLISECONDS: u64 = 10;\n\n"
        "type LogicalSnapshot = (u64, Map<String, Value>, Map<String, Value>);\n",
        "R10 logical snapshot type alias",
    )
    source = replace_once(
        source,
        ") -> Result<(u64, Map<String, Value>, Map<String, Value>), String> {",
        ") -> Result<LogicalSnapshot, String> {",
        "R10 logical snapshot return type",
    )
    source = replace_once(
        source,
        "/// backup bounds, SQLite online backup, or R9 logical validation fails.\n",
        "/// backup bounds, `SQLite` online backup, or R9 logical validation fails.\n",
        "R10 SQLite documentation markup",
    )
    path.write_text(source, encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
