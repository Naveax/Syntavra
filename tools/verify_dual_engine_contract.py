#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "contracts" / "engine" / "descriptor.txt"
RUST_CONTRACTS = ROOT / "crates" / "syntavra-contracts" / "src" / "lib.rs"
CONTRACT_JSON = (
    ROOT / "contracts" / "engine" / "capabilities.schema.json",
    ROOT / "contracts" / "engine" / "selection.schema.json",
    ROOT / "contracts" / "cli" / "result-envelope.schema.json",
    ROOT / "contracts" / "mcp" / "tool-catalog.schema.json",
    ROOT / "contracts" / "state" / "layout.json",
    ROOT / "contracts" / "receipts" / "common.schema.json",
    ROOT / "parity" / "normalizers" / "default.json",
)


def _rust_descriptor() -> str:
    source = RUST_CONTRACTS.read_text(encoding="utf-8")
    marker = "pub const CONTRACT_DESCRIPTOR: &str = concat!("
    if marker not in source:
        raise RuntimeError("Rust contract descriptor marker is missing")
    block = source.split(marker, 1)[1].split(");", 1)[0]
    literals = re.findall(r'"((?:\\.|[^"\\])*)"', block)
    if not literals:
        raise RuntimeError("Rust contract descriptor contains no string literals")
    return "".join(json.loads(f'"{value}"') for value in literals)


def _descriptor_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    capabilities: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise RuntimeError(f"invalid descriptor line: {line!r}")
        if key == "capability":
            capabilities.append(value)
        elif key in fields:
            raise RuntimeError(f"duplicate descriptor key: {key}")
        else:
            fields[key] = value
    fields["capabilities"] = "\n".join(capabilities)
    return fields


def verify() -> dict[str, object]:
    descriptor = DESCRIPTOR.read_text(encoding="utf-8")
    if not descriptor.endswith("\n"):
        raise RuntimeError("contract descriptor must be newline terminated")
    rust_descriptor = _rust_descriptor()
    if descriptor != rust_descriptor:
        raise RuntimeError("Rust embedded descriptor differs from contracts/engine/descriptor.txt")

    fields = _descriptor_fields(descriptor)
    if fields.get("product") != "Syntavra":
        raise RuntimeError("unexpected product identity")
    if fields.get("product_version") != "0.0.1":
        raise RuntimeError("product version changed without owner authorization")
    if fields.get("release_channel") != "pre-release":
        raise RuntimeError("release channel changed without owner authorization")
    if fields.get("contract_version") != "1":
        raise RuntimeError("unexpected initial contract version")

    capability_rows = fields["capabilities"].splitlines()
    if capability_rows != sorted(capability_rows):
        raise RuntimeError("descriptor capabilities must be sorted")

    parsed_contracts: list[str] = []
    for path in CONTRACT_JSON:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"contract must be a JSON object: {path}")
        parsed_contracts.append(path.relative_to(ROOT).as_posix())

    selection = json.loads((ROOT / "contracts" / "engine" / "selection.schema.json").read_text(encoding="utf-8"))
    engine_enum = selection.get("properties", {}).get("engine", {}).get("enum")
    if engine_enum != ["auto", "python", "rust"]:
        raise RuntimeError("engine selection enum or order drifted")

    return {
        "ok": True,
        "contract_version": int(fields["contract_version"]),
        "descriptor_sha256": hashlib.sha256(descriptor.encode("utf-8")).hexdigest(),
        "capabilities": capability_rows,
        "engine_modes": engine_enum,
        "json_contracts": parsed_contracts,
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
