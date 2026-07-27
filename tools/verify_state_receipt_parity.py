#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from syntavra_runtime.state_receipt_contract import (
    ReceiptContractError,
    inspect_receipt_wire,
    state_layout,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "parity" / "fixtures" / "state-receipts-v1.json"
LAYOUT = ROOT / "contracts" / "state" / "layout.json"


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


def _rust_error(*arguments: str) -> str:
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
    if completed.returncode == 0:
        raise RuntimeError("Rust engine unexpectedly accepted invalid R7 fixture")
    return completed.stderr.splitlines()[0].strip()


def verify() -> dict[str, object]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected_layout = json.loads(LAYOUT.read_text(encoding="utf-8"))

    python_layout = state_layout()
    rust_layout = _rust_json("state", "layout")
    if python_layout != expected_layout or rust_layout != expected_layout:
        raise RuntimeError("R7/R8 state-layout parity failed")

    valid_names: list[str] = []
    for row in fixture["valid_receipts"]:
        wire = bytes.fromhex(row["wire_hex"])
        expected = row["expected"]
        python_value = inspect_receipt_wire(
            wire,
            expected_project_id=row["expected_project_id"],
        )
        rust_value = _rust_json(
            "receipt",
            "inspect",
            row["expected_project_id"],
            row["wire_hex"],
        )
        if python_value != expected or rust_value != expected:
            raise RuntimeError(f"R7 valid receipt parity failed: {row['name']}")
        valid_names.append(row["name"])

    invalid_names: list[str] = []
    for row in fixture["invalid_receipts"]:
        wire = bytes.fromhex(row["wire_hex"])
        try:
            inspect_receipt_wire(
                wire,
                expected_project_id=row["expected_project_id"],
            )
        except ReceiptContractError as exc:
            python_error = exc.code
        else:
            raise RuntimeError(f"Python accepted invalid R7 receipt: {row['name']}")
        rust_error = _rust_error(
            "receipt",
            "inspect",
            row["expected_project_id"],
            row["wire_hex"],
        )
        if python_error != row["error"] or rust_error != row["error"]:
            raise RuntimeError(
                f"R7 invalid receipt parity failed: {row['name']}: "
                f"python={python_error!r} rust={rust_error!r}"
            )
        invalid_names.append(row["name"])

    return {
        "ok": True,
        "phase": "R7-R8-layout",
        "state_layout": expected_layout["layout_id"],
        "valid_receipts": valid_names,
        "invalid_receipts": invalid_names,
        "claim": "RUST_STATE_LAYOUT_RECEIPT_PARITY_PROVEN_R7_FIXTURES",
        "boundaries": {
            "filesystem_state_reads_r7": False,
            "filesystem_state_reads_r8": True,
            "filesystem_mutation": False,
            "database_access": False,
        },
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
