#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.telemetry_metrics_contract import telemetry_metrics_result
from syntavra_runtime.unified_cli import main as unified_main

ROOT = Path(__file__).resolve().parents[1]


def _rust(output_format: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "telemetry",
            "metrics",
            output_format,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust telemetry.metrics failed ({completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust telemetry.metrics output must be an object")
    return value


def verify() -> dict[str, object]:
    expected_json = telemetry_metrics_result("json")
    expected_prometheus = telemetry_metrics_result("prometheus")
    candidate_json = _rust("json")
    candidate_prometheus = _rust("prometheus")
    if candidate_json != expected_json:
        raise RuntimeError("JSON telemetry.metrics parity failed")
    if candidate_prometheus != expected_prometheus:
        raise RuntimeError("Prometheus telemetry.metrics parity failed")

    with tempfile.TemporaryDirectory(prefix="syntavra-r24-telemetry-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        if unified_main(["--project", str(project), "telemetry", "metrics"]) != 0:
            raise RuntimeError("Python telemetry.metrics failed")
        if (project / ".syntavra").exists():
            raise RuntimeError("Python telemetry.metrics created state")

    return {
        "ok": True,
        "phase": "R24",
        "command": "telemetry.metrics",
        "capability": "telemetry.metrics",
        "formats": ["json", "prometheus"],
        "state_created": False,
        "fallback_policy": "none",
        "claim": "RUST_TELEMETRY_METRICS_READ_ONLY_CLI_PARITY_PROVEN_R24",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
