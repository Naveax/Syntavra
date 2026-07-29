#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT / "crates"
CLI_MAIN = CRATES / "syntavra-cli" / "src" / "main.rs"
CONTRACTS = CRATES / "syntavra-contracts" / "src" / "lib.rs"

_USAGE = re.compile(r"syntavra-rs\s+([^\\\"\n]+)\\n")
_CAPABILITY = re.compile(r"name:\s*\"([A-Za-z0-9_.:-]+)\"")
_PRODUCT_VERSION = re.compile(r"PRODUCT_VERSION:\s*&str\s*=\s*\"([^\"]+)\"")
_RELEASE_CHANNEL = re.compile(r"RELEASE_CHANNEL:\s*&str\s*=\s*\"([^\"]+)\"")


def _rust_files() -> list[Path]:
    return sorted(path for path in CRATES.rglob("*.rs") if path.is_file())


def _normalize_usage(value: str) -> str:
    return " ".join(value.strip().split())


def export_surface() -> dict[str, object]:
    cli_source = CLI_MAIN.read_text(encoding="utf-8")
    contract_source = CONTRACTS.read_text(encoding="utf-8")
    modules = [path.relative_to(ROOT).as_posix() for path in _rust_files()]
    commands = sorted({_normalize_usage(value) for value in _USAGE.findall(cli_source)})
    capabilities = sorted(set(_CAPABILITY.findall(contract_source)))
    product_version_match = _PRODUCT_VERSION.search(contract_source)
    release_channel_match = _RELEASE_CHANNEL.search(contract_source)

    return {
        "schema_version": 1,
        "engine": "rust",
        "source_root": "crates",
        "module_count": len(modules),
        "modules": modules,
        "cli_usage": commands,
        "capabilities": capabilities,
        "product_version": product_version_match.group(1) if product_version_match else None,
        "release_channel": release_channel_match.group(1) if release_channel_match else None,
        "unsafe_forbidden_files": sum(
            1 for path in _rust_files() if "#![forbid(unsafe_code)]" in path.read_text(encoding="utf-8")
        ),
        "authoritative": {
            "modules": True,
            "capabilities": True,
            "product_version": True,
            "release_channel": True,
            "cli_usage": False
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the deterministic Rust public-surface inventory.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(export_surface(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
