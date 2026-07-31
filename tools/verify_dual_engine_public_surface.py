#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface

CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
VALID_STATUSES = {
    "PYTHON_ONLY",
    "RUST_VIA_PYTHON_LAUNCHER",
    "RUST_NATIVE_PUBLIC",
}
FULL_CLAIM = "FULL_DUAL_ENGINE_PARITY_PROVEN"
INCOMPLETE_CLAIM = "DUAL_ENGINE_PARITY_INCOMPLETE"


def verify(*, require_full: bool = False) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    python_surface = export_python_surface()
    rust_surface = export_rust_surface()

    if contract.get("schema_version") != 2:
        raise RuntimeError("dual-engine contract schema must be 2")
    if contract.get("product") != "Syntavra":
        raise RuntimeError("dual-engine contract product mismatch")
    if contract.get("product_version") != "0.0.1":
        raise RuntimeError("product version must remain 0.0.1")
    if contract.get("release_channel") != "pre-release":
        raise RuntimeError("release channel must remain pre-release")

    rows = contract.get("commands")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("dual-engine command inventory is missing")

    command_rows: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("every dual-engine command row must be an object")
        command = row.get("command")
        status = row.get("rust_status")
        if not isinstance(command, str) or not command:
            raise RuntimeError("every dual-engine command requires a command path")
        if command in command_rows:
            raise RuntimeError(f"duplicate command path: {command}")
        if row.get("python") is not True:
            raise RuntimeError(f"Python ownership missing for {command}")
        if status not in VALID_STATUSES:
            raise RuntimeError(f"invalid Rust status for {command}: {status!r}")
        if status != "PYTHON_ONLY" and not row.get("rust_owner"):
            raise RuntimeError(f"Rust-owned command lacks rust_owner: {command}")
        command_rows[command] = row

    exported_commands = set(python_surface.get("cli_commands", []))
    catalog_commands = set(command_rows)
    if catalog_commands != exported_commands:
        missing = sorted(exported_commands - catalog_commands)
        stale = sorted(catalog_commands - exported_commands)
        raise RuntimeError(
            f"dual-engine command inventory drifted: missing={missing!r}, stale={stale!r}"
        )

    statuses = Counter(str(row["rust_status"]) for row in command_rows.values())
    native = statuses["RUST_NATIVE_PUBLIC"]
    bridged = statuses["RUST_VIA_PYTHON_LAUNCHER"]
    missing = statuses["PYTHON_ONLY"]
    total = len(command_rows)
    full = native == total and bridged == 0 and missing == 0

    inventory = contract.get("inventory")
    if not isinstance(inventory, dict):
        raise RuntimeError("dual-engine inventory summary is missing")
    expected_inventory = {
        "python_module_count": python_surface["module_count"],
        "python_public_command_count": total,
        "rust_module_count": rust_surface["module_count"],
        "rust_native_public_command_count": native,
        "rust_launcher_bridge_command_count": bridged,
        "rust_missing_public_command_count": total - native,
        "rust_native_coverage_ppm": native * 1_000_000 // total,
    }
    for key, value in expected_inventory.items():
        if inventory.get(key) != value:
            raise RuntimeError(
                f"dual-engine inventory drift for {key}: expected {value!r}, got {inventory.get(key)!r}"
            )

    claim = contract.get("claim")
    if full and claim != FULL_CLAIM:
        raise RuntimeError("complete dual-engine coverage must carry the full claim")
    if not full and claim != INCOMPLETE_CLAIM:
        raise RuntimeError("incomplete dual-engine coverage must fail closed")
    if require_full and not full:
        raise RuntimeError(
            f"full dual-engine parity not reached: native={native}/{total}, bridged={bridged}, python_only={missing}"
        )

    return {
        "ok": True,
        "claim": claim,
        "full": full,
        "python": {
            "module_count": python_surface["module_count"],
            "public_command_count": total,
        },
        "rust": {
            "module_count": rust_surface["module_count"],
            "native_public_command_count": native,
            "launcher_bridge_command_count": bridged,
            "python_only_command_count": missing,
            "native_coverage_ppm": native * 1_000_000 // total,
        },
        "policy": contract["policy"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the complete Python/Rust public command inventory."
    )
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="fail unless every Python public command is independently native in Rust",
    )
    args = parser.parse_args()
    print(json.dumps(verify(require_full=args.require_full), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
