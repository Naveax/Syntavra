#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from syntavra_runtime.mcp_policy import MCPToolPolicy
from syntavra_runtime.mcp_server import MCPServer
from syntavra_runtime.tool_registry import ToolSchemaCompiler
from syntavra_runtime.util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "engine" / "mcp-native-catalog-v1.json"
PROFILES = ("minimal", "balanced", "audit")
PIPELINE = [
    "profile-selection",
    "catalog-filter",
    "schema-compilation",
    "authorization",
    "argument-decoding",
    "execution",
    "exact-output-capture",
    "secret-redaction",
    "optional-lossless-wire-encoding",
    "route-receipt",
]


def profile_payload(catalog: list[dict[str, Any]], profile: str) -> dict[str, Any]:
    policy = MCPToolPolicy(profile)
    selected = policy.filter_catalog(catalog)
    compiler = ToolSchemaCompiler()
    compiled, compilation = compiler.compile_catalog(selected)
    compilation_value = compilation.to_dict()
    base_manifest = {
        "pipeline": PIPELINE,
        "policy": {
            "profile": policy.profile,
            "legacy_profile": policy.legacy_profile,
        },
    }
    return {
        "raw_tools": selected,
        "compiled_tools": compiled,
        "compilation": compilation_value,
        "compact_manifest": {
            **base_manifest,
            "schema": {
                "mode": "compact",
                "profile": policy.profile,
                "compilation": compilation_value,
            },
        },
        "raw_manifest": {
            **base_manifest,
            "schema": {
                "mode": "raw",
                "profile": policy.profile,
                "compilation": compilation_value,
            },
        },
    }


def render() -> dict[str, Any]:
    catalog = MCPServer.tools()
    return {
        "schema_version": 1,
        "surface": "syntavra-native-mcp-catalog",
        "server": {
            "name": "syntavra",
            "version": MCPServer.VERSION,
            "default_protocol_version": "2025-06-18",
            "instructions": "Token/context optimization with exact recovery and fail-closed tool routing.",
        },
        "catalog": {
            "tool_count": len(catalog),
            "sha256": sha256_bytes(canonical_json(catalog)),
        },
        "profile_aliases": {
            "minimal": "minimal",
            "tiny": "minimal",
            "balanced": "balanced",
            "optimized": "balanced",
            "audit": "audit",
            "full": "audit",
        },
        "profiles": {profile: profile_payload(catalog, profile) for profile in PROFILES},
    }


def main() -> int:
    value = render()
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    changed = not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != rendered
    if changed:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "profile_counts": {
                    name: len(row["compiled_tools"])
                    for name, row in value["profiles"].items()
                },
                "surface": value["surface"],
                "tool_count": value["catalog"]["tool_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
