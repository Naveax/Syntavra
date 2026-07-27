#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from syntavra_runtime.mcp_server import MCPServer
from syntavra_runtime.prerelease_cli import COMPATIBILITY_COMMANDS, PRIMARY_COMMANDS
from syntavra_runtime.release_identity import CHANNEL, VERSION
from syntavra_runtime.unified_cli import CORE_COMMANDS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "contracts" / "inventory" / "python-reference.json"


def inventory() -> dict[str, object]:
    tools = sorted(str(row["name"]) for row in MCPServer.tools())
    return {
        "schema_version": 1,
        "reference_engine": "python",
        "product": "Syntavra",
        "product_version": VERSION,
        "release_channel": CHANNEL,
        "command_groups": {
            "primary": sorted(PRIMARY_COMMANDS),
            "compatibility": sorted(COMPATIBILITY_COMMANDS),
            "core": sorted(CORE_COMMANDS),
        },
        "mcp_tools": tools,
        "mcp_tool_count": len(tools),
        "status": "reference-inventory",
    }


def render() -> str:
    return json.dumps(inventory(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the Python reference surface for Rust parity work.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if current != expected:
            print(f"Python reference inventory is stale: {args.output}")
            return 1
        print(f"Python reference inventory verified: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Python reference inventory written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
