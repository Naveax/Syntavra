#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r15 import ReadOnlyCommandRouterR15

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v4.json"


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


def _file_identity(path: Path) -> tuple[bytes, int, int]:
    metadata = path.stat()
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r15-") as directory:
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
            "SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF": "secret://ci/provider",
        }
        selector = EngineSelector(
            project_root=project,
            env=env,
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR15(selector, runner=_cargo_rust_json)
        before = (_file_identity(user_config), _file_identity(project_config))
        python_status = router.route("status", cli_override="python", live_config=True)
        rust_status = router.route("status", cli_override="rust", live_config=True)
        python_config = router.route(
            "config.resolve",
            cli_override="python",
            live_config=True,
        )
        rust_config = router.route(
            "config.resolve",
            cli_override="rust",
            live_config=True,
        )
        after = (_file_identity(user_config), _file_identity(project_config))
        last_good = project / ".syntavra" / "pre-release" / "config-last-good.json"

        conflict_error: EngineSelectionError | None = None
        version_error: EngineSelectionError | None = None
        try:
            router.route(
                "status",
                cli_override="rust",
                config_wire_hex="00",
                live_config=True,
            )
        except EngineSelectionError as exc:
            conflict_error = exc
        try:
            router.route("version", cli_override="rust", live_config=True)
        except EngineSelectionError as exc:
            version_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        encoded = json.dumps(
            [python_status, rust_status, python_config, rust_config],
            ensure_ascii=False,
            sort_keys=True,
        )
        input_sha = str(rust_config["input"]["sha256"])
        checks = {
            "contract_schema": contract.get("schema_version") == 4,
            "contract_phase": contract.get("phase") == "R15",
            "live_owner_python": contract.get("live_discovery", {}).get("owner")
            == "python-router",
            "live_read_only": contract.get("live_discovery", {}).get("read_only")
            is True,
            "rust_no_filesystem": contract.get("live_discovery", {}).get(
                "rust_filesystem_access"
            )
            is False,
            "status_profiles": route_rows.get("status", {}).get(
                "accepted_input_profiles"
            )
            == [
                "default-config-only",
                "explicit-config-wire-v1",
                "live-config-discovery-v1",
            ],
            "config_profiles": route_rows.get("config.resolve", {}).get(
                "accepted_input_profiles"
            )
            == ["explicit-config-wire-v1", "live-config-discovery-v1"],
            "status_parity": python_status["result"] == rust_status["result"],
            "config_parity": python_config["result"] == rust_config["result"],
            "phase_upgrade": all(
                route.get("phase") == "R15" and route.get("schema_version") == 4
                for route in (
                    python_status,
                    rust_status,
                    python_config,
                    rust_config,
                )
            ),
            "live_profile": all(
                route.get("input", {}).get("profile") == "live-config-discovery-v1"
                for route in (
                    python_status,
                    rust_status,
                    python_config,
                    rust_config,
                )
            ),
            "project_precedence": rust_config["result"]["values"]["runtime"][
                "profile"
            ]
            == "detailed",
            "environment_precedence": rust_config["result"]["values"]["provider"][
                "timeout_seconds"
            ]
            == 90.0,
            "secret_ref_redacted": any(
                row.get("path") == "provider.credential_ref"
                and row.get("value") == "[secret-ref]"
                for row in rust_config["result"]["provenance"]
            ),
            "source_unchanged": before == after,
            "last_good_not_written": not last_good.exists(),
            "raw_wire_absent": input_sha in encoded
            and all(len(str(route["input"]["sha256"])) == 64 for route in (
                python_status,
                rust_status,
                python_config,
                rust_config,
            )),
            "input_conflict_fails_closed": conflict_error is not None
            and conflict_error.code == "ENGINE_ROUTE_INPUT_CONFLICT_R15"
            and conflict_error.details.get("fallback_attempted") is False,
            "version_live_rejected": version_error is not None
            and version_error.code == "ENGINE_ROUTE_LIVE_CONFIG_UNSUPPORTED_R15"
            and version_error.details.get("fallback_attempted") is False,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R15 live config routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R15",
            "checks": checks,
            "routes": ["config.resolve", "status", "version"],
            "input_profile": "live-config-discovery-v1",
            "input_sha256": input_sha,
            "config_hash": rust_config["result"]["config_hash"],
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_LIVE_CONFIG_DISCOVERY_ROUTING_PARITY_PROVEN_R15",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
