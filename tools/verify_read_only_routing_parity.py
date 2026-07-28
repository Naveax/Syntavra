#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.config_contract import (
    MAX_CONFIG_WIRE_BYTES,
    encode_config_wire,
    resolve_config_phases,
    status_projection,
)
from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router import ReadOnlyCommandRouter

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v2.json"
FIXTURE = ROOT / "parity" / "fixtures" / "config-status-v1.json"


def _cargo_rust_json(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


def _shared_version_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "product",
            "product_version",
            "release_channel",
            "contract_version",
        )
    }


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    selector = EngineSelector(
        project_root=ROOT,
        env={"HOME": str(ROOT / ".syntavra-r13-verifier-home")},
        rust_binary=ROOT / "Cargo.toml",
        runner=_cargo_rust_json,
    )
    router = ReadOnlyCommandRouter(selector, runner=_cargo_rust_json)

    python_version = router.route("version", cli_override="python")
    rust_version = router.route("version", cli_override="rust")
    python_default_status = router.route("status", cli_override="python")
    rust_default_status = router.route("status", cli_override="rust")

    case_results: list[dict[str, object]] = []
    for case in fixture.get("cases", []):
        name = str(case.get("name") or "")
        phases = case.get("phases")
        if not name or not isinstance(phases, list):
            raise RuntimeError("R13 fixture case requires name and phases")
        wire = encode_config_wire(phases)
        expected = status_projection(resolve_config_phases(phases))
        python_route = router.route(
            "status",
            cli_override="python",
            config_wire_hex=wire.hex(),
        )
        rust_route = router.route(
            "status",
            cli_override="rust",
            config_wire_hex=wire.hex(),
        )
        if python_route["result"] != expected or rust_route["result"] != expected:
            raise RuntimeError(f"R13 status route parity failed for {name}")
        if python_route["input"] != rust_route["input"]:
            raise RuntimeError(f"R13 input metadata parity failed for {name}")
        case_results.append(
            {
                "name": name,
                "wire_bytes": len(wire),
                "wire_sha256": hashlib.sha256(wire).hexdigest(),
                "config_hash": expected["config_hash"],
                "warnings": expected["warnings"],
            }
        )

    unsupported_error: EngineSelectionError | None = None
    invalid_input_error: EngineSelectionError | None = None
    version_input_error: EngineSelectionError | None = None
    try:
        router.route("config.resolve", cli_override="rust")
    except EngineSelectionError as exc:
        unsupported_error = exc
    try:
        router.route("status", cli_override="rust", config_wire_hex="abc")
    except EngineSelectionError as exc:
        invalid_input_error = exc
    try:
        router.route("version", cli_override="rust", config_wire_hex="00")
    except EngineSelectionError as exc:
        version_input_error = exc

    expected_success_keys = set(contract["success_envelope"]["required"])
    route_rows = contract.get("routes", [])
    route_names = [
        str(row.get("command"))
        for row in route_rows
        if isinstance(row, dict)
    ]
    route_by_name = {
        str(row.get("command")): row
        for row in route_rows
        if isinstance(row, dict)
    }
    successful_routes = (
        python_version,
        rust_version,
        python_default_status,
        rust_default_status,
    )
    status_profiles = route_by_name.get("status", {}).get("accepted_input_profiles")
    checks = {
        "contract_schema": contract.get("schema_version") == 2,
        "contract_phase": contract.get("phase") == "R13",
        "route_inventory": route_names == ["status", "version"],
        "bounded_input": contract.get("maximum_input_bytes")
        == MAX_CONFIG_WIRE_BYTES,
        "status_input_profiles": status_profiles
        == ["default-config-only", "explicit-config-wire-v1"],
        "version_input_profile": route_by_name.get("version", {}).get(
            "accepted_input_profiles"
        )
        == ["none"],
        "exact_success_envelopes": all(
            set(route) == expected_success_keys for route in successful_routes
        ),
        "shared_version_fields": _shared_version_fields(python_version["result"])
        == _shared_version_fields(rust_version["result"]),
        "default_status_parity": python_default_status["result"]
        == rust_default_status["result"],
        "fixture_count": len(case_results) >= 4,
        "input_metadata_no_raw": all(
            set(route["input"]) == {"profile", "format", "bytes", "sha256"}
            for route in successful_routes
        ),
        "read_only_mutation": all(
            route.get("mutation") == "read-only" for route in successful_routes
        ),
        "no_success_fallback": all(
            route.get("fallback") == {"policy": "none", "attempted": False}
            for route in successful_routes
        ),
        "unsupported_fails_closed": unsupported_error is not None
        and unsupported_error.code == "ENGINE_ROUTE_UNSUPPORTED_R13",
        "invalid_input_fails_before_routing": invalid_input_error is not None
        and invalid_input_error.code == "ENGINE_ROUTE_INPUT_INVALID_R13",
        "version_input_rejected": version_input_error is not None
        and version_input_error.code == "ENGINE_ROUTE_INPUT_UNSUPPORTED_R13",
        "all_errors_no_fallback": all(
            error is not None
            and error.details.get("fallback_attempted") is False
            and error.details.get("fallback_policy") == "none"
            for error in (
                unsupported_error,
                invalid_input_error,
                version_input_error,
            )
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"R13 read-only routing parity failed: {checks}")

    return {
        "ok": True,
        "phase": "R13",
        "checks": checks,
        "routes": ["status", "version"],
        "status_input_profiles": status_profiles,
        "maximum_input_bytes": MAX_CONFIG_WIRE_BYTES,
        "cases": case_results,
        "fallback_policy": "none",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "claim": "RUST_EXPLICIT_CONFIG_STATUS_ROUTING_PARITY_PROVEN_R13",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
