#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} marker, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    wiring_path = ROOT / "tools" / "r10_wire_source.py"
    wiring = wiring_path.read_text(encoding="utf-8")
    wiring = replace_once(
        wiring,
        '        "contract_string",\n',
        "",
        "generic contract_string wiring entry",
    )
    wiring = replace_once(
        wiring,
        '        "contract_array",\n',
        "",
        "generic contract_array wiring entry",
    )
    wiring_path.write_text(wiring, encoding="utf-8", newline="\n")

    rust_path = ROOT / "crates" / "syntavra-cli" / "src" / "broker_snapshot_contract.rs"
    rust = rust_path.read_text(encoding="utf-8")
    rust = replace_once(
        rust,
        "fn contract_string<'a>(",
        "pub(crate) fn contract_string<'a>(",
        "generic contract_string visibility",
    )
    rust = replace_once(
        rust,
        "fn contract_array<'a>(",
        "pub(crate) fn contract_array<'a>(",
        "generic contract_array visibility",
    )
    rust_path.write_text(rust, encoding="utf-8", newline="\n")

    import r10_wire_source

    return r10_wire_source.main()


if __name__ == "__main__":
    raise SystemExit(main())
