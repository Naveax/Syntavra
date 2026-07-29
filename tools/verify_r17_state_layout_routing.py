#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r17 import ReadOnlyCommandRouterR17
from syntavra_runtime.state_receipt_contract import state_layout

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v6.json"


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


def _inventory(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r17-") as directory:
        project = Path(directory)
        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(project / "home")},
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR17(selector, runner=_cargo_rust_json)
        before = _inventory(project)
        python_layout = router.route("state.layout", cli_override="python")
        rust_layout = router.route("state.layout", cli_override="rust")
        after = _inventory(project)

        input_error: EngineSelectionError | None = None
        try:
            router.route(
                "state.layout",
                cli_override="rust",
                live_config=True,
            )
        except EngineSelectionError as exc:
            input_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        state_row = route_rows.get("state.layout", {})
        state_policy = contract.get("state_layout_route", {})
        checks = {
            "contract_schema": contract.get("schema_version") == 6,
            "contract_phase": contract.get("phase") == "R17",
            "route_inventory": sorted(route_rows)
            == ["config.resolve", "state.layout", "status", "version"],
            "state_capability": state_row.get("required_capability") == "state.layout",
            "state_read_only": state_row.get("mutation") == "read-only",
            "state_input_none": state_row.get("accepted_input_profiles") == ["none"],
            "state_rust_argv": state_row.get("rust_argv", {}).get("none")
            == ["state", "layout"],
            "python_authority": state_policy.get("python_authority")
            == "syntavra_runtime.state_receipt_contract.state_layout",
            "no_filesystem_access": state_policy.get("filesystem_access") is False,
            "no_database_access": state_policy.get("database_access") is False,
            "no_mutation": state_policy.get("mutation") is False,
            "exact_comparison": state_policy.get("comparison")
            == "exact-complete-object",
            "python_reference": python_layout["result"] == state_layout(),
            "cross_engine_parity": python_layout["result"] == rust_layout["result"],
            "phase_upgrade": python_layout.get("phase") == "R17"
            and rust_layout.get("phase") == "R17"
            and python_layout.get("schema_version") == 6
            and rust_layout.get("schema_version") == 6,
            "input_metadata_none": rust_layout.get("input")
            == {
                "profile": "none",
                "format": None,
                "bytes": 0,
                "sha256": None,
            },
            "selection_rust": rust_layout.get("selection", {}).get("resolved") == "rust",
            "no_project_state_access": before == after == [],
            "input_rejected": input_error is not None
            and input_error.code == "ENGINE_ROUTE_STATE_LAYOUT_INPUT_UNSUPPORTED_R17"
            and input_error.details.get("fallback_attempted") is False,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R17 state.layout routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R17",
            "checks": checks,
            "routes": ["config.resolve", "state.layout", "status", "version"],
            "input_profile": "none",
            "layout_id": rust_layout["result"]["layout_id"],
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_STATE_LAYOUT_ROUTING_PARITY_PROVEN_R17",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
