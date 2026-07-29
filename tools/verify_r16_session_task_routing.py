#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r16 import ReadOnlyCommandRouterR16
from syntavra_runtime.util import canonical_json

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v5.json"


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


def _override_hex(value: Mapping[str, Any]) -> str:
    return canonical_json(value).hex()


def _file_identity(path: Path) -> tuple[bytes, int, int]:
    metadata = path.stat()
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r16-") as directory:
        project = Path(directory)
        home = project / "home"
        user_config = home / ".config" / "syntavra" / "config.toml"
        project_config = project / ".syntavra" / "config.toml"
        user_config.parent.mkdir(parents=True)
        project_config.parent.mkdir(parents=True)
        user_config.write_bytes(b'[runtime]\nprofile = "compact"\n')
        project_config.write_bytes(
            b'[runtime]\nprofile = "detailed"\n[routing]\nbudget_bytes = 4096\n'
        )
        env = {
            "HOME": str(home),
            "SYNTAVRA_CFG__PROVIDER__TIMEOUT_SECONDS": "90.0",
        }
        selector = EngineSelector(
            project_root=project,
            env=env,
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR16(selector, runner=_cargo_rust_json)
        session_hex = _override_hex(
            {
                "provider": {"timeout_seconds": 45.0},
                "runtime": {"profile": "audit"},
            }
        )
        task_hex = _override_hex(
            {
                "routing": {"budget_bytes": 16384},
                "runtime": {"profile": "terse"},
            }
        )
        before = (_file_identity(user_config), _file_identity(project_config))
        python_status = router.route(
            "status",
            cli_override="python",
            live_config=True,
            session_override_json_hex=session_hex,
            task_override_json_hex=task_hex,
        )
        rust_status = router.route(
            "status",
            cli_override="rust",
            live_config=True,
            session_override_json_hex=session_hex,
            task_override_json_hex=task_hex,
        )
        python_config = router.route(
            "config.resolve",
            cli_override="python",
            live_config=True,
            session_override_json_hex=session_hex,
            task_override_json_hex=task_hex,
        )
        rust_config = router.route(
            "config.resolve",
            cli_override="rust",
            live_config=True,
            session_override_json_hex=session_hex,
            task_override_json_hex=task_hex,
        )
        after = (_file_identity(user_config), _file_identity(project_config))
        last_good = project / ".syntavra" / "pre-release" / "config-last-good.json"

        requires_live_error: EngineSelectionError | None = None
        invalid_error: EngineSelectionError | None = None
        try:
            router.route(
                "status",
                cli_override="rust",
                session_override_json_hex=session_hex,
            )
        except EngineSelectionError as exc:
            requires_live_error = exc
        invalid_hex = b'{"runtime": {"profile": "audit"}}'.hex()
        try:
            router.route(
                "config.resolve",
                cli_override="rust",
                live_config=True,
                task_override_json_hex=invalid_hex,
            )
        except EngineSelectionError as exc:
            invalid_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        rendered = json.dumps(
            [
                python_status,
                rust_status,
                python_config,
                rust_config,
                requires_live_error.to_dict() if requires_live_error else {},
                invalid_error.to_dict() if invalid_error else {},
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        checks = {
            "contract_schema": contract.get("schema_version") == 5,
            "contract_phase": contract.get("phase") == "R16",
            "override_bound": contract.get("maximum_override_json_bytes") == 65536,
            "override_owner_python": contract.get("session_task_overrides", {}).get(
                "owner"
            )
            == "python-router",
            "rust_final_wire_only": contract.get("session_task_overrides", {}).get(
                "rust_receives_final_r6cfg1_only"
            )
            is True,
            "status_profile_admitted": "live-config-session-task-v1"
            in route_rows.get("status", {}).get("accepted_input_profiles", []),
            "config_profile_admitted": "live-config-session-task-v1"
            in route_rows.get("config.resolve", {}).get("accepted_input_profiles", []),
            "status_parity": python_status["result"] == rust_status["result"],
            "config_parity": python_config["result"] == rust_config["result"],
            "phase_upgrade": all(
                route.get("phase") == "R16" and route.get("schema_version") == 5
                for route in (
                    python_status,
                    rust_status,
                    python_config,
                    rust_config,
                )
            ),
            "override_profile": all(
                route.get("input", {}).get("profile")
                == "live-config-session-task-v1"
                for route in (
                    python_status,
                    rust_status,
                    python_config,
                    rust_config,
                )
            ),
            "task_precedence": rust_config["result"]["values"]["runtime"]["profile"]
            == "terse",
            "session_precedence": rust_config["result"]["values"]["provider"][
                "timeout_seconds"
            ]
            == 45.0,
            "task_routing_precedence": rust_config["result"]["values"]["routing"][
                "budget_bytes"
            ]
            == 16384,
            "session_provenance": any(
                row.get("path") == "provider.timeout_seconds"
                and row.get("scope") == "session"
                for row in rust_config["result"]["provenance"]
            ),
            "task_provenance": any(
                row.get("path") == "runtime.profile" and row.get("scope") == "task"
                for row in rust_config["result"]["provenance"]
            ),
            "source_unchanged": before == after,
            "last_good_not_written": not last_good.exists(),
            "raw_overrides_absent": session_hex not in rendered
            and task_hex not in rendered
            and invalid_hex not in rendered,
            "requires_live_fails_closed": requires_live_error is not None
            and requires_live_error.code
            == "ENGINE_ROUTE_OVERRIDE_REQUIRES_LIVE_CONFIG_R16"
            and requires_live_error.details.get("fallback_attempted") is False,
            "noncanonical_fails_closed": invalid_error is not None
            and invalid_error.code == "ENGINE_ROUTE_OVERRIDE_INVALID_R16"
            and invalid_error.details.get("fallback_attempted") is False,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R16 session/task routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R16",
            "checks": checks,
            "routes": ["config.resolve", "status", "version"],
            "input_profile": "live-config-session-task-v1",
            "config_hash": rust_config["result"]["config_hash"],
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_SESSION_TASK_OVERRIDE_ROUTING_PARITY_PROVEN_R16",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
