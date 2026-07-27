#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import r9_wire_source

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} marker, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    r9_wire_source.main()

    path = ROOT / "crates" / "syntavra-cli" / "src" / "broker_snapshot_contract.rs"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "            | b'~' => output.push(char::from(byte)),\n",
        "            | b'~' => {\n"
        "                output.push(char::from(byte));\n"
        "            }\n",
        "R9 percent encoder semicolon",
    )
    path.write_text(source, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
