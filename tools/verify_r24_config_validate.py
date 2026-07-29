#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_validate_contract import validation_result
from syntavra_runtime.live_config_discovery import discover_live_config_wire
from syntavra_runtime.unified_config import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def _rust_snapshot(wire: bytes) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "config",
            "resolve",
            wire.hex(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust config.resolve failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust config.resolve output must be a JSON object")
    return value


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-config-validate-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        config = project / ".syntavra" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            '[runtime]\nprofile = "compact"\n\n[routing]\nbudget_bytes = 4096\n',
            encoding="utf-8",
        )
        environment = {"HOME": str(root / "home")}
        wire = discover_live_config_wire(project_root=project, env=environment)
        python_snapshot = resolve_config_wire(wire)
        rust_snapshot = _rust_snapshot(wire)
        if rust_snapshot != python_snapshot:
            raise RuntimeError("Python/Rust live config snapshots differ")
        result = validation_result(python_snapshot)
        if set(result) != {"ok", "config_hash", "warnings"} or result["ok"] is not True:
            raise RuntimeError("config.validate result contract drifted")
        if (project / ".syntavra" / "pre-release").exists():
            raise RuntimeError("config.validate created product state")

        config.write_text('[runtime]\nprofile = "invalid"\n', encoding="utf-8")
        invalid_wire = discover_live_config_wire(project_root=project, env=environment)
        invalid_failed = False
        try:
            resolve_config_wire(invalid_wire)
        except ConfigError:
            invalid_failed = True
        if not invalid_failed:
            raise RuntimeError("invalid live config did not fail closed")
        if (project / ".syntavra" / "pre-release").exists():
            raise RuntimeError("invalid config validation created product state")

        return {
            "ok": True,
            "phase": "R24",
            "command": "config.validate",
            "candidate_capability": "config.resolve",
            "input_profile": "live-config-discovery-v1",
            "input_format": "R6CFG1",
            "wire_bytes": len(wire),
            "config_hash": result["config_hash"],
            "invalid_config_fail_closed": invalid_failed,
            "filesystem_write": False,
            "fallback_policy": "none",
            "claim": "RUST_CONFIG_VALIDATE_READ_ONLY_PARITY_PROVEN_R24",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
