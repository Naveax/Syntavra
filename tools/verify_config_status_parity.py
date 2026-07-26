#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from syntavra_runtime.config_contract import (
    encode_config_wire,
    resolve_config_phases,
    status_projection,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "parity" / "fixtures" / "config-status-v1.json"


def _rust_json(*arguments: str) -> dict[str, Any]:
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
            f"Rust R6 command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust R6 output must be a JSON object")
    return value


def verify() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1:
        raise RuntimeError("unsupported R6 fixture schema")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or len(cases) < 4:
        raise RuntimeError("R6 fixture must contain at least four cases")

    results: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise RuntimeError("R6 fixture case must be an object")
        name = str(case.get("name") or "")
        phases = case.get("phases")
        if not name or not isinstance(phases, list):
            raise RuntimeError("R6 fixture case requires name and phases")

        reference_config = resolve_config_phases(phases)
        wire = encode_config_wire(phases)
        candidate_config = _rust_json("config", "resolve", wire.hex())
        if candidate_config != reference_config:
            raise RuntimeError(
                f"R6 config parity failed for {name}: "
                f"reference={reference_config!r} candidate={candidate_config!r}"
            )

        reference_status = status_projection(reference_config)
        candidate_status = _rust_json("status", wire.hex())
        if candidate_status != reference_status:
            raise RuntimeError(
                f"R6 status parity failed for {name}: "
                f"reference={reference_status!r} candidate={candidate_status!r}"
            )

        if name == "defaults":
            default_status = _rust_json("status")
            if default_status != reference_status:
                raise RuntimeError(
                    f"R6 default status command drifted: {default_status!r}"
                )

        results.append(
            {
                "name": name,
                "config_hash": reference_config["config_hash"],
                "warnings": reference_config["warnings"],
                "provenance_entries": len(reference_config["provenance"]),
                "wire_sha256": hashlib.sha256(wire).hexdigest(),
            }
        )

    return {
        "ok": True,
        "phase": "R6",
        "contract": fixture.get("contract"),
        "reference_engine": "python",
        "candidate_engine": "rust",
        "cases": results,
        "checks": {
            "config_precedence": True,
            "config_provenance": True,
            "config_hash": True,
            "last_good_fallback": True,
            "identity": True,
            "status": True,
        },
        "claim": "RUST_CONFIG_IDENTITY_STATUS_PARITY_PROVEN_R6_FIXTURES",
        "boundaries": [
            "RUST_GENERAL_COMMAND_ROUTING_BLOCKED_R4",
            "RUST_STATE_COMPATIBILITY_NOT_PROVEN",
            "RUST_MCP_PARITY_NOT_PROVEN",
            "RUST_PROCESS_PARITY_NOT_PROVEN",
        ],
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
