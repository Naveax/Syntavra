#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router import ReadOnlyCommandRouter

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v1.json"


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
    selector = EngineSelector(
        project_root=ROOT,
        env={"HOME": str(ROOT / ".syntavra-r12-verifier-home")},
        rust_binary=ROOT / "Cargo.toml",
        runner=_cargo_rust_json,
    )
    router = ReadOnlyCommandRouter(selector, runner=_cargo_rust_json)

    python_version = router.route("version", cli_override="python")
    rust_version = router.route("version", cli_override="rust")
    python_status = router.route("status", cli_override="python")
    rust_status = router.route("status", cli_override="rust")

    unsupported_error: EngineSelectionError | None = None
    try:
        router.route("config.resolve", cli_override="rust")
    except EngineSelectionError as exc:
        unsupported_error = exc

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
    successful_routes = (python_version, rust_version, python_status, rust_status)
    checks = {
        "contract_schema": contract.get("schema_version") == 1,
        "contract_phase": contract.get("phase") == "R12",
        "route_inventory": route_names == ["status", "version"],
        "status_fixed_default_input": route_by_name.get("status", {}).get("input_profile")
        == "default-config-only"
        and route_by_name.get("status", {}).get("rust_argv") == ["status"],
        "version_fixed_input": route_by_name.get("version", {}).get("input_profile")
        == "none"
        and route_by_name.get("version", {}).get("rust_argv") == ["version"],
        "exact_success_envelopes": all(
            set(route) == expected_success_keys for route in successful_routes
        ),
        "shared_version_fields": _shared_version_fields(python_version["result"])
        == _shared_version_fields(rust_version["result"]),
        "python_reference_selected": python_version["result"].get("engine") == "python",
        "rust_binary_selected": rust_version["result"].get("engine") == "rust",
        "default_status_parity": python_status["result"] == rust_status["result"],
        "default_status_locked_boundary": python_status["result"].get(
            "general_command_routing"
        )
        == "blocked"
        and python_status["result"].get("mutation") == "read-only",
        "read_only_mutation": all(
            route.get("mutation") == "read-only" for route in successful_routes
        ),
        "no_success_fallback": all(
            route.get("fallback") == {"policy": "none", "attempted": False}
            for route in successful_routes
        ),
        "unsupported_fails_closed": unsupported_error is not None
        and unsupported_error.code == "ENGINE_ROUTE_UNSUPPORTED_R12",
        "unsupported_no_fallback": unsupported_error is not None
        and unsupported_error.details.get("fallback_attempted") is False
        and unsupported_error.details.get("fallback_policy") == "none",
    }
    if not all(checks.values()):
        raise RuntimeError(f"R12 read-only routing parity failed: {checks}")

    return {
        "ok": True,
        "phase": "R12",
        "checks": checks,
        "routes": ["status", "version"],
        "status_input_profile": "default-config-only",
        "fallback_policy": "none",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "claim": "RUST_READ_ONLY_VERSION_STATUS_ROUTING_PARITY_PROVEN_R12",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
