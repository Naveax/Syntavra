#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from syntavra_runtime.release_identity import identity

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "contracts" / "engine" / "descriptor.txt"


def _rust_json(*arguments: str) -> dict[str, object]:
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


def verify() -> dict[str, object]:
    reference = identity().to_dict()
    version = _rust_json("version")
    capabilities = _rust_json("engine", "capabilities")
    contract_hash = _rust_json("engine", "contract-hash")

    checks = {
        "product": version.get("product") == "Syntavra",
        "version": version.get("product_version") == reference["version"],
        "channel": version.get("release_channel") == reference["channel"],
        "rust_engine": version.get("engine") == "rust",
        "contract_version": version.get("contract_version") == 1,
        "capability_contract": capabilities.get("contract_version") == 1,
        "descriptor_hash": contract_hash.get("contract_hash")
        == hashlib.sha256(DESCRIPTOR.read_bytes()).hexdigest(),
    }
    if not all(checks.values()):
        raise RuntimeError(f"initial Python/Rust parity failed: {checks}")

    capability_names = [
        str(row.get("name"))
        for row in capabilities.get("capabilities", [])
        if isinstance(row, dict)
    ]
    expected = ["engine.capabilities", "engine.contract-hash", "version"]
    if capability_names != expected:
        raise RuntimeError(
            f"unexpected initial Rust capability surface: {capability_names!r}"
        )

    return {
        "ok": True,
        "phase": "R0-R3",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "checks": checks,
        "capabilities": capability_names,
        "claim": "RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
