#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r22 import (
    AUTO_POLICY,
    AUTO_ROUTE_CAPABILITIES,
    ReadOnlyCommandRouterR22,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v11.json"


def _cargo_rust_json(
    _binary: Path,
    arguments: tuple[str, ...],
) -> Mapping[str, Any]:
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
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust engine command failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


def _selector(
    project: Path,
    *,
    runner=_cargo_rust_json,
) -> EngineSelector:
    return EngineSelector(
        project_root=project,
        env={"HOME": str(project / "home")},
        rust_binary=ROOT / "Cargo.toml",
        runner=runner,
    )


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r22-") as directory:
        project = Path(directory) / "project"
        project.mkdir()

        candidate_calls: list[tuple[str, ...]] = []

        def candidate_runner(
            binary: Path,
            arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            candidate_calls.append(arguments)
            return _cargo_rust_json(binary, arguments)

        router = ReadOnlyCommandRouterR22(
            _selector(project),
            runner=candidate_runner,
            project_input_root=project,
            platform_probe=lambda: ("linux", "x86_64"),
        )
        auto_rust = router.route("version", cli_override="auto")
        explicit_python = router.route("version", cli_override="python")

        incompatible_candidate_calls: list[tuple[str, ...]] = []

        def incompatible_verification(
            binary: Path,
            arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            value = dict(_cargo_rust_json(binary, arguments))
            if arguments == ("engine", "contract-hash"):
                value["contract_hash"] = "0" * 64
            return value

        def incompatible_candidate(
            binary: Path,
            arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            incompatible_candidate_calls.append(arguments)
            return _cargo_rust_json(binary, arguments)

        incompatible_router = ReadOnlyCommandRouterR22(
            _selector(project, runner=incompatible_verification),
            runner=incompatible_candidate,
            project_input_root=project,
            platform_probe=lambda: ("linux", "x86_64"),
        )
        auto_python = incompatible_router.route("version", cli_override="auto")

        unsupported_verification_calls: list[tuple[str, ...]] = []

        def unsupported_verification(
            binary: Path,
            arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            unsupported_verification_calls.append(arguments)
            return _cargo_rust_json(binary, arguments)

        unsupported_router = ReadOnlyCommandRouterR22(
            _selector(project, runner=unsupported_verification),
            runner=candidate_runner,
            project_input_root=project,
            platform_probe=lambda: ("freebsd", "x86_64"),
        )
        unsupported_platform = unsupported_router.route(
            "version",
            cli_override="auto",
        )

        explicit_rust_error: EngineSelectionError | None = None
        try:
            incompatible_router.route("version", cli_override="rust")
        except EngineSelectionError as exc:
            explicit_rust_error = exc

        candidate_failure: EngineSelectionError | None = None

        def failing_candidate(
            _binary: Path,
            _arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            raise RuntimeError("sensitive R22 candidate failure")

        failing_router = ReadOnlyCommandRouterR22(
            _selector(project),
            runner=failing_candidate,
            project_input_root=project,
            platform_probe=lambda: ("linux", "x86_64"),
        )
        try:
            failing_router.route("version", cli_override="auto")
        except EngineSelectionError as exc:
            candidate_failure = exc

        preflight_verification_calls: list[tuple[str, ...]] = []

        def preflight_verification(
            binary: Path,
            arguments: tuple[str, ...],
        ) -> Mapping[str, Any]:
            preflight_verification_calls.append(arguments)
            return _cargo_rust_json(binary, arguments)

        preflight_router = ReadOnlyCommandRouterR22(
            _selector(project, runner=preflight_verification),
            project_input_root=project,
            platform_probe=lambda: ("linux", "x86_64"),
        )
        preflight_error: EngineSelectionError | None = None
        try:
            preflight_router.route(
                "state.broker-snapshot",
                cli_override="auto",
            )
        except EngineSelectionError as exc:
            preflight_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        policy = contract.get("auto_selection", {})
        rendered_failure = json.dumps(
            candidate_failure.to_dict() if candidate_failure else {},
            sort_keys=True,
        )
        routes = list(sorted(AUTO_ROUTE_CAPABILITIES))
        checks = {
            "contract_schema": contract.get("schema_version") == 11,
            "contract_phase": contract.get("phase") == "R22",
            "default_python": contract.get("default_engine") == "python",
            "route_inventory": sorted(route_rows) == routes,
            "all_routes_read_only": all(
                row.get("mutation") == "read-only" for row in route_rows.values()
            ),
            "policy_id": policy.get("policy_id") == AUTO_POLICY,
            "selection_boundary": policy.get("selection_boundary")
            == "after-python-route-preflight-before-rust-candidate-execution",
            "eligible_routes": policy.get("eligible_commands") == routes,
            "auto_selected_rust": auto_rust.get("selection", {}).get("requested")
            == "auto"
            and auto_rust.get("selection", {}).get("resolved") == "rust"
            and auto_rust.get("selection", {}).get("reason")
            == "AUTO_ROUTE_RUST_SELECTED_R22",
            "candidate_executed": candidate_calls == [("version",)],
            "explicit_python_unchanged": explicit_python.get("selection", {}).get(
                "resolved"
            )
            == "python"
            and explicit_python.get("result", {}).get("engine") == "python",
            "contract_failure_selected_python_before_candidate": auto_python.get(
                "selection", {}
            ).get("resolved")
            == "python"
            and auto_python.get("selection", {}).get("reason")
            == "AUTO_ROUTE_CONTRACT_INCOMPATIBLE_R22"
            and incompatible_candidate_calls == [],
            "unsupported_platform_selected_python_without_verification": (
                unsupported_platform.get("selection", {}).get("resolved") == "python"
                and unsupported_platform.get("selection", {}).get("reason")
                == "AUTO_ROUTE_PLATFORM_UNSUPPORTED_R22"
                and unsupported_verification_calls == []
            ),
            "explicit_rust_failed_closed": explicit_rust_error is not None
            and explicit_rust_error.code == "RUST_ENGINE_UNAVAILABLE_R14"
            and explicit_rust_error.details.get("fallback_attempted") is False,
            "candidate_failure_no_fallback": candidate_failure is not None
            and candidate_failure.code == "RUST_ROUTE_EXECUTION_FAILED_R14"
            and candidate_failure.details.get("fallback_attempted") is False
            and "sensitive R22 candidate failure" not in rendered_failure,
            "route_preflight_before_auto_verification": preflight_error is not None
            and preflight_error.code
            == "ENGINE_ROUTE_BROKER_DATABASE_INPUT_REQUIRED_R20"
            and preflight_verification_calls == [],
            "general_auto_remains_python": _selector(project).resolve(
                cli_override="auto"
            ).resolved
            == "python",
            "phase_upgrade": auto_rust.get("phase") == "R22"
            and auto_rust.get("schema_version") == 11,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R22 capability-aware auto parity failed: {checks}")

        return {
            "ok": True,
            "phase": "R22",
            "checks": checks,
            "routes": routes,
            "auto_policy": AUTO_POLICY,
            "default_engine": "python",
            "selected_engine": "rust",
            "fallback_policy": "none-after-selection",
            "claim": "RUST_ROUTE_SCOPED_CAPABILITY_AWARE_AUTO_R22",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
