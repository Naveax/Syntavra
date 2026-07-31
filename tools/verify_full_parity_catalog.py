#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface

CATALOG = ROOT / "contracts" / "parity" / "python-rust-full-parity-v1.json"
ALLOWED_STATUSES = {
    "PYTHON_ONLY",
    "RUST_SCAFFOLDED",
    "RUST_SHADOW",
    "PARITY_PROVEN",
    "RUST_PRODUCTION_READY",
}
EXPECTED_PHASES = [f"R{value}" for value in range(23, 38)]
PROVEN_ROUTE_CAPABILITIES = {
    "route.version": "version",
    "route.status": "status",
    "route.config.explain": "config.explain",
    "route.config.resolve": "config.resolve",
    "route.config.validate": "config.resolve",
    "route.state.layout": "state.layout",
    "route.state.inspect": "state.inspect",
    "route.receipt.inspect": "receipt.inspect",
    "route.state.broker-snapshot": "state.broker-snapshot",
    "route.state.broker-live-snapshot": "state.broker-live-snapshot",
    "route.pipeline.describe": "pipeline.describe",
    "route.plugins.list": "plugins.list",
}
REQUIRED_PYTHON_COMMANDS = {
    "agent",
    "backup",
    "config",
    "maintenance",
    "migrate",
    "pipeline",
    "plugins",
    "scheduler",
    "telemetry",
}
DIMENSION_CATEGORIES = {
    "cli": {"cli"},
    "mcp": {"mcp"},
    "state_mutation": {"state_mutation"},
    "host_setup": {"host_setup"},
    "platform_packaging": {"platform_packaging"},
}


def _load_catalog() -> dict[str, object]:
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("parity catalog must be a JSON object")
    return value


def _feature_map(catalog: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = catalog.get("features")
    if not isinstance(rows, list):
        raise RuntimeError("catalog features must be a list")
    output: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("every catalog feature must be an object")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("every catalog feature requires a non-empty id")
        if identifier in output:
            raise RuntimeError(f"duplicate catalog feature: {identifier}")
        output[identifier] = row
    return output


def _dimension_report(features: dict[str, dict[str, object]]) -> dict[str, object]:
    report: dict[str, object] = {}
    for dimension, categories in DIMENSION_CATEGORIES.items():
        rows = [row for row in features.values() if row.get("category") in categories]
        proven = [
            row
            for row in rows
            if row.get("status") in {"PARITY_PROVEN", "RUST_PRODUCTION_READY"}
        ]
        ready = [row for row in rows if row.get("status") == "RUST_PRODUCTION_READY"]
        report[dimension] = {
            "total": len(rows),
            "parity_proven": len(proven),
            "production_ready": len(ready),
            "complete": bool(rows) and len(ready) == len(rows),
        }
    return report


def verify() -> dict[str, object]:
    catalog = _load_catalog()
    python_surface = export_python_surface()
    rust_surface = export_rust_surface()
    features = _feature_map(catalog)

    if catalog.get("schema_version") != 1:
        raise RuntimeError("unexpected parity catalog schema version")
    if catalog.get("product") != "Syntavra":
        raise RuntimeError("unexpected parity catalog product")
    if catalog.get("product_version") != "0.0.1":
        raise RuntimeError("product version must remain locked to 0.0.1")
    if catalog.get("release_channel") != "pre-release":
        raise RuntimeError("release channel must remain pre-release")
    if catalog.get("claim") != "FULL_PARITY_PROVEN":
        raise RuntimeError("R37 catalog must carry the certified full-parity claim")
    if rust_surface.get("product_version") != catalog.get("product_version"):
        raise RuntimeError("Rust product version drifted from parity catalog")
    if rust_surface.get("release_channel") != catalog.get("release_channel"):
        raise RuntimeError("Rust release channel drifted from parity catalog")
    if python_surface.get("parse_failures"):
        raise RuntimeError(f"Python surface parse failures: {python_surface['parse_failures']!r}")

    workstreams = catalog.get("workstreams")
    if not isinstance(workstreams, list):
        raise RuntimeError("catalog workstreams must be a list")
    phases = [row.get("phase") for row in workstreams if isinstance(row, dict)]
    if phases != EXPECTED_PHASES:
        raise RuntimeError(f"workstream phases must be exactly {EXPECTED_PHASES!r}, got {phases!r}")
    if any(row.get("status") != "COMPLETE" for row in workstreams if isinstance(row, dict)):
        raise RuntimeError("every R23-R37 workstream must be COMPLETE after R37 certification")

    rust_capabilities = set(rust_surface.get("capabilities", []))
    for identifier, capability in PROVEN_ROUTE_CAPABILITIES.items():
        row = features.get(identifier)
        if row is None:
            raise RuntimeError(f"missing proven route catalog entry: {identifier}")
        if row.get("status") not in {"PARITY_PROVEN", "RUST_PRODUCTION_READY"}:
            raise RuntimeError(f"proven route lost parity status: {identifier}")
        if capability not in rust_capabilities:
            raise RuntimeError(f"Rust capability missing for {identifier}: {capability}")

    for identifier, row in features.items():
        status = row.get("status")
        if status not in ALLOWED_STATUSES:
            raise RuntimeError(f"invalid status for {identifier}: {status!r}")
        phase = row.get("target_phase")
        if not isinstance(phase, str) or not phase.startswith("R"):
            raise RuntimeError(f"invalid target phase for {identifier}")
        if status in {"PARITY_PROVEN", "RUST_PRODUCTION_READY"}:
            if not row.get("rust_owner"):
                raise RuntimeError(f"proven feature lacks Rust owner: {identifier}")
            if not row.get("contract"):
                raise RuntimeError(f"proven feature lacks contract: {identifier}")
            tests = row.get("parity_tests")
            if not isinstance(tests, list) or not tests:
                raise RuntimeError(f"proven feature lacks parity tests: {identifier}")
            for relative in tests:
                if not isinstance(relative, str) or not (ROOT / relative).is_file():
                    raise RuntimeError(f"missing parity test for {identifier}: {relative!r}")

    python_commands = set(python_surface.get("cli_commands", []))
    missing_commands = sorted(REQUIRED_PYTHON_COMMANDS - python_commands)
    if missing_commands:
        raise RuntimeError(f"Python CLI exporter missed baseline commands: {missing_commands!r}")

    status_counts = Counter(str(row.get("status")) for row in features.values())
    dimensions = _dimension_report(features)
    if not all(bool(row.get("complete")) for row in dimensions.values()):
        raise RuntimeError("every parity dimension must be production-ready at R37")

    if any(row.get("status") != "RUST_PRODUCTION_READY" for row in features.values()):
        raise RuntimeError("every catalogued feature must be production-ready at R37")

    certification = catalog.get("certification")
    if not isinstance(certification, dict):
        raise RuntimeError("R37 certification evidence is missing")
    if certification.get("phase") != "R37":
        raise RuntimeError("certification phase must be R37")
    if certification.get("python_invocation_by_rust") is not False:
        raise RuntimeError("Rust must remain Python-independent")
    if certification.get("platforms") != ["linux-x64", "windows-x64", "macos-x64", "macos-arm64"]:
        raise RuntimeError("R36/R37 platform evidence inventory drifted")
    for key in ("core_exact_parity", "resilience", "standalone"):
        relative = certification.get(key)
        if not isinstance(relative, str) or not (ROOT / relative).is_file():
            raise RuntimeError(f"missing certification verifier: {key}")

    return {
        "ok": True,
        "phase": "R37",
        "program": "R23-R37",
        "claim": catalog["claim"],
        "feature_count": len(features),
        "status_counts": dict(sorted(status_counts.items())),
        "workstreams": EXPECTED_PHASES,
        "dimensions": dimensions,
        "python_surface": {
            "module_count": python_surface["module_count"],
            "cli_command_count": len(python_surface["cli_commands"]),
            "environment_variable_count": len(python_surface["environment_variables"]),
        },
        "rust_surface": {
            "module_count": rust_surface["module_count"],
            "capabilities": rust_surface["capabilities"],
            "unsafe_forbidden_files": rust_surface["unsafe_forbidden_files"],
        },
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
