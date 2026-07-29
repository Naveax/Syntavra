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
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v3.json"
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
        env={"HOME": str(ROOT / ".syntavra-r14-verifier-home")},
        rust_binary=ROOT / "Cargo.toml",
        runner=_cargo_rust_json,
    )
    router = ReadOnlyCommandRouter(selector, runner=_cargo_rust_json)

    python_version = router.route("version", cli_override="python")
    rust_version = router.route("version", cli_override="rust")
    python_default_status = router.route("status", cli_override="python")
    rust_default_status = router.route("status", cli_override="rust")

    case_results: list[dict[str, object]] = []
    successful_routes: list[Mapping[str, Any]] = [
        python_version,
        rust_version,
        python_default_status,
        rust_default_status,
    ]
    for case in fixture.get("cases", []):
        name = str(case.get("name") or "")
        phases = case.get("phases")
        if not name or not isinstance(phases, list):
            raise RuntimeError("R14 fixture case requires name and phases")
        wire = encode_config_wire(phases)
        expected_snapshot = resolve_config_phases(phases)
        expected_status = status_projection(expected_snapshot)

        python_status = router.route(
            "status",
            cli_override="python",
            config_wire_hex=wire.hex(),
        )
        rust_status = router.route(
            "status",
            cli_override="rust",
            config_wire_hex=wire.hex(),
        )
        python_config = router.route(
            "config.resolve",
            cli_override="python",
            config_wire_hex=wire.hex(),
        )
        rust_config = router.route(
            "config.resolve",
            cli_override="rust",
            config_wire_hex=wire.hex(),
        )

        if python_status["result"] != expected_status or rust_status["result"] != expected_status:
            raise RuntimeError(f"R14 status route parity failed for {name}")
        if python_config["result"] != expected_snapshot or rust_config["result"] != expected_snapshot:
            raise RuntimeError(f"R14 config.resolve route parity failed for {name}")
        if not (
            python_status["input"]
            == rust_status["input"]
            == python_config["input"]
            == rust_config["input"]
        ):
            raise RuntimeError(f"R14 input metadata parity failed for {name}")
        encoded_envelopes = json.dumps(
            [python_status, rust_status, python_config, rust_config],
            ensure_ascii=False,
            sort_keys=True,
        )
        if wire.hex() in encoded_envelopes:
            raise RuntimeError(f"R14 route envelope exposed raw wire for {name}")

        successful_routes.extend(
            [python_status, rust_status, python_config, rust_config]
        )
        case_results.append(
            {
                "name": name,
                "wire_bytes": len(wire),
                "wire_sha256": hashlib.sha256(wire).hexdigest(),
                "config_hash": expected_snapshot["config_hash"],
                "warnings": expected_snapshot["warnings"],
            }
        )

    unsupported_error: EngineSelectionError | None = None
    missing_input_error: EngineSelectionError | None = None
    invalid_input_error: EngineSelectionError | None = None
    version_input_error: EngineSelectionError | None = None
    try:
        router.route("state.layout", cli_override="rust")
    except EngineSelectionError as exc:
        unsupported_error = exc
    try:
        router.route("config.resolve", cli_override="rust")
    except EngineSelectionError as exc:
        missing_input_error = exc
    try:
        router.route("config.resolve", cli_override="rust", config_wire_hex="abc")
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
    status_profiles = route_by_name.get("status", {}).get("accepted_input_profiles")
    config_profiles = route_by_name.get("config.resolve", {}).get(
        "accepted_input_profiles"
    )
    checks = {
        "contract_schema": contract.get("schema_version") == 3,
        "contract_phase": contract.get("phase") == "R14",
        "route_inventory": route_names == ["config.resolve", "status", "version"],
        "bounded_input": contract.get("maximum_input_bytes")
        == MAX_CONFIG_WIRE_BYTES,
        "config_input_profile": config_profiles == ["explicit-config-wire-v1"],
        "config_rust_argv": route_by_name.get("config.resolve", {})
        .get("rust_argv", {})
        .get("explicit-config-wire-v1")
        == ["config", "resolve", "<config-wire-hex>"],
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
        and unsupported_error.code == "ENGINE_ROUTE_UNSUPPORTED_R14",
        "config_input_required": missing_input_error is not None
        and missing_input_error.code == "ENGINE_ROUTE_INPUT_REQUIRED_R14",
        "invalid_input_fails_before_routing": invalid_input_error is not None
        and invalid_input_error.code == "ENGINE_ROUTE_INPUT_INVALID_R14",
        "version_input_rejected": version_input_error is not None
        and version_input_error.code == "ENGINE_ROUTE_INPUT_UNSUPPORTED_R14",
        "all_errors_no_fallback": all(
            error is not None
            and error.details.get("fallback_attempted") is False
            and error.details.get("fallback_policy") == "none"
            for error in (
                unsupported_error,
                missing_input_error,
                invalid_input_error,
                version_input_error,
            )
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"R14 read-only routing parity failed: {checks}")

    return {
        "ok": True,
        "phase": "R14",
        "checks": checks,
        "routes": ["config.resolve", "status", "version"],
        "config_input_profiles": config_profiles,
        "status_input_profiles": status_profiles,
        "maximum_input_bytes": MAX_CONFIG_WIRE_BYTES,
        "cases": case_results,
        "fallback_policy": "none",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "claim": "RUST_EXPLICIT_CONFIG_RESOLVE_ROUTING_PARITY_PROVEN_R14",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
