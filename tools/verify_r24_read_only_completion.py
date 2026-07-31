#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface

CATALOG_PATH = ROOT / "contracts" / "parity" / "python-rust-full-parity-v1.json"
CONTRACT_PATH = ROOT / "contracts" / "parity" / "r24-read-only-cli-completion-v1.json"
PROVEN_STATUSES = {"PARITY_PROVEN", "RUST_PRODUCTION_READY"}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _feature_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = catalog.get("features")
    if not isinstance(rows, list):
        raise RuntimeError("catalog features must be a list")
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("every catalog feature must be an object")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError("every catalog feature requires an id")
        if identifier in output:
            raise RuntimeError(f"duplicate catalog feature: {identifier}")
        output[identifier] = row
    return output


def _route_rows(contract: dict[str, Any]) -> list[dict[str, str]]:
    rows = contract.get("routes")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("completion contract routes must be a non-empty list")
    output: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("every completion route must be an object")
        normalized: dict[str, str] = {}
        for key in ("feature_id", "python_command", "rust_capability"):
            value = row.get(key)
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"completion route requires {key}")
            normalized[key] = value
        output.append(normalized)
    feature_ids = [row["feature_id"] for row in output]
    if len(feature_ids) != len(set(feature_ids)):
        raise RuntimeError("completion contract contains duplicate feature ids")
    return output


def _run_verifier(relative: str) -> dict[str, object]:
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"missing real-binary verifier: {relative}")
    completed = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"real-binary verifier failed: {relative}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return {
        "path": relative,
        "returncode": completed.returncode,
        "stdout_sha256": __import__("hashlib").sha256(
            completed.stdout.encode("utf-8")
        ).hexdigest(),
    }


def verify(*, run_real_verifiers: bool = False) -> dict[str, object]:
    catalog = _load_object(CATALOG_PATH)
    contract = _load_object(CONTRACT_PATH)
    features = _feature_map(catalog)
    routes = _route_rows(contract)
    python_surface = export_python_surface()
    rust_surface = export_rust_surface()

    if contract.get("schema_version") != 1:
        raise RuntimeError("unexpected R24 completion contract schema")
    if contract.get("product") != "Syntavra":
        raise RuntimeError("unexpected R24 completion product")
    if contract.get("product_version") != "0.0.1":
        raise RuntimeError("R24 completion must remain locked to 0.0.1")
    if contract.get("release_channel") != "pre-release":
        raise RuntimeError("R24 completion must remain pre-release")
    if contract.get("claim") != "READ_ONLY_CLI_PARITY_PROVEN":
        raise RuntimeError("unexpected R24 completion claim")
    if contract.get("full_product_parity") != "FULL_PARITY_NOT_PROVEN":
        raise RuntimeError("R24 must not claim full product parity")

    completion_id = contract.get("completion_feature")
    completion = features.get(str(completion_id))
    if completion is None:
        raise RuntimeError("catalog is missing cli.read-only.complete")
    if completion.get("status") not in PROVEN_STATUSES:
        raise RuntimeError("cli.read-only.complete must remain parity-proven")
    if completion.get("mutation") != "read-only":
        raise RuntimeError("cli.read-only.complete must remain read-only")
    if completion.get("contract") != CONTRACT_PATH.relative_to(ROOT).as_posix():
        raise RuntimeError("cli.read-only.complete contract path drifted")
    parity_tests = completion.get("parity_tests")
    relative_self = Path(__file__).relative_to(ROOT).as_posix()
    if parity_tests != [relative_self]:
        raise RuntimeError("cli.read-only.complete parity verifier drifted")

    scope = contract.get("catalog_scope")
    if not isinstance(scope, dict):
        raise RuntimeError("completion contract catalog_scope must be an object")
    target_phases = set(scope.get("target_phases", []))
    excluded = set(scope.get("exclude_feature_ids", []))
    scoped_catalog_ids = {
        identifier
        for identifier, row in features.items()
        if row.get("target_phase") in target_phases
        and row.get("category") == scope.get("category")
        and row.get("mutation") == scope.get("mutation")
        and identifier not in excluded
    }
    expected_ids = {row["feature_id"] for row in routes}
    if scoped_catalog_ids != expected_ids:
        raise RuntimeError(
            "R24 read-only route inventory drifted: "
            f"missing={sorted(expected_ids - scoped_catalog_ids)!r}, "
            f"unexpected={sorted(scoped_catalog_ids - expected_ids)!r}"
        )

    rust_capabilities = set(rust_surface.get("capabilities", []))
    python_commands = set(python_surface.get("cli_commands", []))
    missing_python_commands: list[str] = []
    missing_rust_capabilities: list[str] = []

    for route in routes:
        identifier = route["feature_id"]
        row = features[identifier]
        if row.get("status") not in PROVEN_STATUSES:
            raise RuntimeError(f"R24 route lost parity proof: {identifier}")
        if not row.get("rust_owner"):
            raise RuntimeError(f"R24 route lacks Rust owner: {identifier}")
        contract_path = row.get("contract")
        if not isinstance(contract_path, str) or not (ROOT / contract_path).is_file():
            raise RuntimeError(f"R24 route contract missing: {identifier}")
        tests = row.get("parity_tests")
        if not isinstance(tests, list) or not tests:
            raise RuntimeError(f"R24 route lacks parity tests: {identifier}")
        for relative in tests:
            if not isinstance(relative, str) or not (ROOT / relative).is_file():
                raise RuntimeError(f"R24 route parity test missing: {identifier}: {relative!r}")
        if route["rust_capability"] not in rust_capabilities:
            missing_rust_capabilities.append(route["rust_capability"])
        if route["python_command"] not in python_commands:
            missing_python_commands.append(route["python_command"])

    if missing_rust_capabilities:
        raise RuntimeError(
            f"Rust capability coverage incomplete: {sorted(set(missing_rust_capabilities))!r}"
        )
    if missing_python_commands:
        raise RuntimeError(
            f"Python CLI exporter missed certified commands: {sorted(set(missing_python_commands))!r}"
        )

    verifier_paths = contract.get("real_binary_verifiers")
    if not isinstance(verifier_paths, list) or not verifier_paths:
        raise RuntimeError("completion contract requires real_binary_verifiers")
    verifier_results: list[dict[str, object]] = []
    for relative in verifier_paths:
        if not isinstance(relative, str) or not relative:
            raise RuntimeError("invalid real-binary verifier path")
        if not (ROOT / relative).is_file():
            raise RuntimeError(f"missing real-binary verifier: {relative}")
        if run_real_verifiers:
            verifier_results.append(_run_verifier(relative))

    return {
        "ok": True,
        "phase": "R24",
        "claim": contract["claim"],
        "full_product_parity": contract["full_product_parity"],
        "certified_route_count": len(routes),
        "certified_feature_ids": sorted(expected_ids),
        "required_rust_capabilities": sorted(
            {row["rust_capability"] for row in routes}
        ),
        "real_binary_verifier_count": len(verifier_paths),
        "real_binary_verifiers_executed": run_real_verifiers,
        "real_binary_results": verifier_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify complete R24 read-only Python/Rust CLI parity."
    )
    parser.add_argument(
        "--run-real-verifiers",
        action="store_true",
        help="run every underlying real-binary parity verifier",
    )
    args = parser.parse_args()
    print(json.dumps(verify(run_real_verifiers=args.run_real_verifiers), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
