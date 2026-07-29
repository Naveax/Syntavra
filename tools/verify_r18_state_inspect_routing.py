#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r18 import ReadOnlyCommandRouterR18
from syntavra_runtime.state_snapshot_contract import (
    MAX_FILE_BYTES,
    inspect_state_root,
    project_id_for_root,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v7.json"


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


def _populate_state(project: Path) -> None:
    state = project / ".syntavra"
    (state / "pre-release").mkdir(parents=True)
    (state / "runtime-v3").mkdir()
    (state / "config.toml").write_bytes(b'[runtime]\nprofile = "audit"\n')
    (state / "engine.json").write_bytes(b'{"schema_version":1,"engine":"python"}\n')


def _inventory(root: Path) -> dict[str, tuple[str, int | None, str | None]]:
    rows: dict[str, tuple[str, int | None, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[relative] = ("symlink", None, None)
        elif path.is_dir():
            rows[relative] = ("directory", None, None)
        elif path.is_file():
            payload = path.read_bytes()
            rows[relative] = ("file", len(payload), hashlib.sha256(payload).hexdigest())
    return rows


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r18-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        _populate_state(project)
        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(Path(directory) / "home")},
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR18(
            selector,
            runner=_cargo_rust_json,
            project_input_root=project,
        )
        before = _inventory(project)
        python_inspection = router.route("state.inspect", cli_override="python")
        rust_inspection = router.route("state.inspect", cli_override="rust")
        after = _inventory(project)
        project_id = project_id_for_root(project)

        input_error: EngineSelectionError | None = None
        symlink_error: EngineSelectionError | None = None
        try:
            router.route(
                "state.inspect",
                cli_override="rust",
                live_config=True,
            )
        except EngineSelectionError as exc:
            input_error = exc

        link = Path(directory) / "project-link"
        link.symlink_to(project, target_is_directory=True)
        symlink_router = ReadOnlyCommandRouterR18(
            selector,
            runner=_cargo_rust_json,
            project_input_root=link,
        )
        try:
            symlink_router.route("state.inspect", cli_override="rust")
        except EngineSelectionError as exc:
            symlink_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        state_row = route_rows.get("state.inspect", {})
        state_policy = contract.get("state_inspect_route", {})
        rendered = json.dumps(
            [
                python_inspection,
                rust_inspection,
                input_error.to_dict() if input_error else {},
                symlink_error.to_dict() if symlink_error else {},
            ],
            sort_keys=True,
        )
        checks = {
            "contract_schema": contract.get("schema_version") == 7,
            "contract_phase": contract.get("phase") == "R18",
            "route_inventory": sorted(route_rows)
            == ["config.resolve", "state.inspect", "state.layout", "status", "version"],
            "state_capability": state_row.get("required_capability") == "state.inspect",
            "state_read_only": state_row.get("mutation") == "read-only",
            "state_input_profile": state_row.get("accepted_input_profiles")
            == ["project-bound-state-root-v1"],
            "state_rust_argv": state_row.get("rust_argv", {}).get(
                "project-bound-state-root-v1"
            )
            == ["state", "inspect", "<derived-project-id>", "<selected-project-root>"],
            "python_authority": state_policy.get("python_authority")
            == "syntavra_runtime.state_snapshot_contract.inspect_state_root",
            "project_binding": state_policy.get("project_id_derivation")
            == "sha256-normalized-canonical-absolute-path",
            "root_symlink_rejected": state_policy.get("project_root_symlink")
            == "reject-before-selection",
            "known_paths_only": state_policy.get("known_paths_only") is True,
            "bounded_file_read": state_policy.get("maximum_file_bytes")
            == MAX_FILE_BYTES,
            "no_recursive_read": state_policy.get("recursive_directory_read") is False,
            "no_database_access": state_policy.get("database_access") is False,
            "no_mutation": state_policy.get("mutation") is False,
            "python_reference": python_inspection["result"]
            == inspect_state_root(project, expected_project_id=project_id),
            "cross_engine_parity": python_inspection["result"]
            == rust_inspection["result"],
            "phase_upgrade": python_inspection.get("phase") == "R18"
            and rust_inspection.get("phase") == "R18"
            and python_inspection.get("schema_version") == 7
            and rust_inspection.get("schema_version") == 7,
            "project_id_metadata": rust_inspection.get("input")
            == {
                "profile": "project-bound-state-root-v1",
                "format": "sha256-normalized-absolute-path-v1",
                "bytes": 32,
                "sha256": project_id,
            },
            "selection_rust": rust_inspection.get("selection", {}).get("resolved")
            == "rust",
            "source_unchanged": before == after,
            "database_never_opened": rust_inspection["result"]["mutation"].get(
                "database_opened"
            )
            is False,
            "input_rejected": input_error is not None
            and input_error.code == "ENGINE_ROUTE_STATE_INSPECT_INPUT_UNSUPPORTED_R18"
            and input_error.details.get("fallback_attempted") is False,
            "symlink_rejected": symlink_error is not None
            and symlink_error.code == "ENGINE_ROUTE_STATE_INSPECT_PREFLIGHT_FAILED_R18"
            and symlink_error.details.get("state_error") == "STATE_PROJECT_ROOT_SYMLINK"
            and symlink_error.details.get("fallback_attempted") is False,
            "source_path_redacted": str(project) not in rendered
            and str(link) not in rendered,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R18 state.inspect routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R18",
            "checks": checks,
            "routes": [
                "config.resolve",
                "state.inspect",
                "state.layout",
                "status",
                "version",
            ],
            "input_profile": "project-bound-state-root-v1",
            "project_id": project_id,
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_STATE_INSPECT_ROUTING_PARITY_PROVEN_R18",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
