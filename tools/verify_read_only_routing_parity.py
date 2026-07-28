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
        env={"HOME": str(ROOT / ".syntavra-r11-verifier-home")},
        rust_binary=ROOT / "Cargo.toml",
        runner=_cargo_rust_json,
    )
    router = ReadOnlyCommandRouter(selector, runner=_cargo_rust_json)

    python_route = router.route("version", cli_override="python")
    rust_route = router.route("version", cli_override="rust")

    unsupported_error: EngineSelectionError | None = None
    try:
        router.route("status", cli_override="rust")
    except EngineSelectionError as exc:
        unsupported_error = exc

    expected_success_keys = set(contract["success_envelope"]["required"])
    route_rows = contract.get("routes", [])
    checks = {
        "contract_schema": contract.get("schema_version") == 1,
        "contract_phase": contract.get("phase") == "R11",
        "single_initial_route": isinstance(route_rows, list)
        and len(route_rows) == 1
        and route_rows[0].get("command") == "version",
        "python_success_envelope": set(python_route) == expected_success_keys,
        "rust_success_envelope": set(rust_route) == expected_success_keys,
        "shared_version_fields": _shared_version_fields(python_route["result"])
        == _shared_version_fields(rust_route["result"]),
        "python_reference_selected": python_route["result"].get("engine") == "python",
        "rust_binary_selected": rust_route["result"].get("engine") == "rust",
        "read_only_mutation": python_route.get("mutation") == "read-only"
        and rust_route.get("mutation") == "read-only",
        "no_success_fallback": python_route.get("fallback")
        == {"policy": "none", "attempted": False}
        and rust_route.get("fallback") == {"policy": "none", "attempted": False},
        "unsupported_fails_closed": unsupported_error is not None
        and unsupported_error.code == "ENGINE_ROUTE_UNSUPPORTED_R11",
        "unsupported_no_fallback": unsupported_error is not None
        and unsupported_error.details.get("fallback_attempted") is False
        and unsupported_error.details.get("fallback_policy") == "none",
    }
    if not all(checks.values()):
        raise RuntimeError(f"R11 read-only routing parity failed: {checks}")

    return {
        "ok": True,
        "phase": "R11",
        "checks": checks,
        "routes": ["version"],
        "fallback_policy": "none",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "claim": "RUST_READ_ONLY_VERSION_ROUTING_PARITY_PROVEN_R11",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
