#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.read_only_router_r19 import (
    MAX_RECEIPT_WIRE_BYTES,
    ReadOnlyCommandRouterR19,
)
from syntavra_runtime.state_receipt_contract import inspect_receipt_wire
from syntavra_runtime.state_snapshot_contract import project_id_for_root

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "read-only-routing-v8.json"


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


def _receipt_wire(project_id: str) -> bytes:
    lines = [
        "R7RCPT1",
        "schema_version=1",
        "product_version=0.0.1",
        "contract_version=1",
        "engine=python",
        f"operation_hex={'state.inspect'.encode('utf-8').hex()}",
        "created_at_ms=1720000000000",
        f"project_id={project_id}",
        f"receipt_id_hex={'receipt-r19-verifier'.encode('utf-8').hex()}",
        f"payload_hash={'1' * 64}",
        "previous_hash=-",
        "fallback_from=-",
        "fallback_to=-",
        "fallback_reason_hex=",
        "fallback_state_mutated=false",
    ]
    material = ("\n".join(lines) + "\n").encode("utf-8")
    receipt_hash = hashlib.sha256(material).hexdigest()
    return material + f"receipt_hash={receipt_hash}\n".encode("utf-8")


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-r19-") as directory:
        project = Path(directory) / "project"
        project.mkdir()
        project_id = project_id_for_root(project)
        wire = _receipt_wire(project_id)
        selector = EngineSelector(
            project_root=project,
            env={"HOME": str(Path(directory) / "home")},
            rust_binary=ROOT / "Cargo.toml",
            runner=_cargo_rust_json,
        )
        router = ReadOnlyCommandRouterR19(
            selector,
            runner=_cargo_rust_json,
            project_input_root=project,
        )
        before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
        python_receipt = router.route(
            "receipt.inspect",
            cli_override="python",
            receipt_wire_hex=wire.hex(),
        )
        rust_receipt = router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=wire.hex(),
        )
        after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))

        uppercase_error: EngineSelectionError | None = None
        replay_error: EngineSelectionError | None = None
        symlink_error: EngineSelectionError | None = None
        try:
            router.route(
                "receipt.inspect",
                cli_override="rust",
                receipt_wire_hex=wire.hex().upper(),
            )
        except EngineSelectionError as exc:
            uppercase_error = exc

        other = Path(directory) / "other"
        other.mkdir()
        replay_wire = _receipt_wire(project_id_for_root(other))
        try:
            router.route(
                "receipt.inspect",
                cli_override="rust",
                receipt_wire_hex=replay_wire.hex(),
            )
        except EngineSelectionError as exc:
            replay_error = exc

        link = Path(directory) / "project-link"
        link.symlink_to(project, target_is_directory=True)
        link_router = ReadOnlyCommandRouterR19(
            selector,
            runner=_cargo_rust_json,
            project_input_root=link,
        )
        try:
            link_router.route(
                "receipt.inspect",
                cli_override="rust",
                receipt_wire_hex=wire.hex(),
            )
        except EngineSelectionError as exc:
            symlink_error = exc

        route_rows = {
            str(row.get("command")): row
            for row in contract.get("routes", [])
            if isinstance(row, dict)
        }
        receipt_row = route_rows.get("receipt.inspect", {})
        receipt_policy = contract.get("receipt_inspect_route", {})
        rendered = json.dumps(
            [
                python_receipt,
                rust_receipt,
                uppercase_error.to_dict() if uppercase_error else {},
                replay_error.to_dict() if replay_error else {},
                symlink_error.to_dict() if symlink_error else {},
            ],
            sort_keys=True,
        )
        checks = {
            "contract_schema": contract.get("schema_version") == 8,
            "contract_phase": contract.get("phase") == "R19",
            "route_inventory": sorted(route_rows)
            == [
                "config.resolve",
                "receipt.inspect",
                "state.inspect",
                "state.layout",
                "status",
                "version",
            ],
            "receipt_capability": receipt_row.get("required_capability")
            == "receipt.inspect",
            "receipt_read_only": receipt_row.get("mutation") == "read-only",
            "receipt_input_profile": receipt_row.get("accepted_input_profiles")
            == ["project-bound-receipt-wire-v1"],
            "receipt_rust_argv": receipt_row.get("rust_argv", {}).get(
                "project-bound-receipt-wire-v1"
            )
            == [
                "receipt",
                "inspect",
                "<derived-project-id>",
                "<canonical-receipt-wire-hex>",
            ],
            "python_authority": receipt_policy.get("python_authority")
            == "syntavra_runtime.state_receipt_contract.inspect_receipt_wire",
            "bounded_wire": receipt_policy.get("maximum_wire_bytes")
            == MAX_RECEIPT_WIRE_BYTES,
            "project_binding": receipt_policy.get("project_id_derivation")
            == "sha256-normalized-canonical-absolute-path",
            "project_replay_rejected": receipt_policy.get("project_replay") == "reject",
            "no_filesystem_state_read": receipt_policy.get("filesystem_state_read") is False,
            "no_database_access": receipt_policy.get("database_access") is False,
            "no_mutation": receipt_policy.get("mutation") is False,
            "python_reference": python_receipt["result"]
            == inspect_receipt_wire(wire, expected_project_id=project_id),
            "cross_engine_parity": python_receipt["result"] == rust_receipt["result"],
            "phase_upgrade": python_receipt.get("phase") == "R19"
            and rust_receipt.get("phase") == "R19"
            and python_receipt.get("schema_version") == 8
            and rust_receipt.get("schema_version") == 8,
            "input_metadata": rust_receipt.get("input")
            == {
                "profile": "project-bound-receipt-wire-v1",
                "format": "R7RCPT1-lowercase-hex-v1",
                "bytes": len(wire),
                "sha256": hashlib.sha256(wire).hexdigest(),
            },
            "selection_rust": rust_receipt.get("selection", {}).get("resolved") == "rust",
            "project_state_unchanged": before == after == [],
            "uppercase_rejected": uppercase_error is not None
            and uppercase_error.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
            and uppercase_error.details.get("receipt_error")
            == "RECEIPT_ROUTE_HEX_NONCANONICAL"
            and uppercase_error.details.get("fallback_attempted") is False,
            "replay_rejected": replay_error is not None
            and replay_error.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
            and replay_error.details.get("receipt_error") == "RECEIPT_PROJECT_MISMATCH"
            and replay_error.details.get("fallback_attempted") is False,
            "root_symlink_rejected": symlink_error is not None
            and symlink_error.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
            and symlink_error.details.get("receipt_error") == "STATE_PROJECT_ROOT_SYMLINK"
            and symlink_error.details.get("fallback_attempted") is False,
            "raw_wire_redacted": wire.hex() not in rendered
            and replay_wire.hex() not in rendered,
            "project_path_redacted": str(project) not in rendered
            and str(link) not in rendered,
        }
        if not all(checks.values()):
            raise RuntimeError(f"R19 receipt.inspect routing parity failed: {checks}")
        return {
            "ok": True,
            "phase": "R19",
            "checks": checks,
            "routes": [
                "config.resolve",
                "receipt.inspect",
                "state.inspect",
                "state.layout",
                "status",
                "version",
            ],
            "input_profile": "project-bound-receipt-wire-v1",
            "receipt_sha256": hashlib.sha256(wire).hexdigest(),
            "fallback_policy": "none",
            "reference_engine": "python",
            "candidate_engine": "rust",
            "claim": "RUST_RECEIPT_INSPECT_ROUTING_PARITY_PROVEN_R19",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
