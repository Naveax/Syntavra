#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.util import canonical_json
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract
from tools.certify_python_reference_suite import run_suite

FREEZE_RELATIVE = Path("contracts/python/python-behavior-freeze-v1.json")
FREEZE_HASH_RELATIVE = Path("contracts/python/python-behavior-freeze-v1.sha256")


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _assert_no_full_route_copy(value: Any) -> None:
    if isinstance(value, list):
        if len(value) == 245 and all(isinstance(item, str) for item in value):
            raise AssertionError("behavior freeze contract must not duplicate the canonical 245-route list")
        for item in value:
            _assert_no_full_route_copy(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_full_route_copy(item)


def _expected_hash(contract: dict[str, Any], key: str, observed: str) -> None:
    strict = bool(contract.get("strict"))
    expected = (contract.get("derived_freeze") or {}).get(key)
    if expected is None:
        if strict:
            raise AssertionError(f"strict behavior freeze is missing derived hash: {key}")
        return
    if not isinstance(expected, str) or len(expected) != 64:
        raise AssertionError(f"invalid expected derived hash for {key}: {expected!r}")
    if observed != expected:
        raise AssertionError(f"derived behavior freeze drift for {key}: {observed} != {expected}")


def _load_and_verify_freeze(repo: Path) -> tuple[dict[str, Any], str]:
    freeze_path = repo / FREEZE_RELATIVE
    hash_path = repo / FREEZE_HASH_RELATIVE
    contract = _read_json(freeze_path)
    if contract.get("schema_version") != 1 or contract.get("family") != "python-behavior-freeze":
        raise AssertionError("Python behavior freeze identity drift")
    if contract.get("phase") != "S":
        raise AssertionError("Python behavior freeze phase drift")
    _assert_no_full_route_copy(contract)

    observed = _sha256_bytes(freeze_path.read_bytes())
    parts = hash_path.read_text(encoding="utf-8").strip().split()
    if not parts or len(parts[0]) != 64:
        raise AssertionError("invalid Python behavior freeze companion SHA-256 file")
    if observed != parts[0]:
        raise AssertionError(f"behavior freeze file hash drift: {observed} != {parts[0]}")
    return contract, observed


def _verify_static_authorities(repo: Path, contract: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    catalog_cfg = contract["fixture_catalog"]
    catalog_path = repo / str(catalog_cfg["path"])
    catalog = _read_json(catalog_path)
    catalog_file_sha = _sha256_bytes(catalog_path.read_bytes())
    catalog_semantic_sha = _semantic_sha(catalog)
    if catalog_file_sha != catalog_cfg["expected_file_sha256"]:
        raise AssertionError("fixture catalog file hash drift")
    if catalog_semantic_sha != catalog_cfg["expected_semantic_sha256"]:
        raise AssertionError("fixture catalog semantic hash drift")
    families = list(catalog.get("families") or [])
    if len(families) != int(catalog_cfg["expected_family_count"]):
        raise AssertionError("fixture catalog family count drift")
    fixture_count = sum(len((row.get("fixtures") or {})) for row in families)
    if fixture_count != int(catalog_cfg["expected_fixture_case_count"]):
        raise AssertionError(f"fixture catalog case count drift: {fixture_count}")

    suite_cfg = contract["reference_suite"]
    suite_contract_path = repo / str(suite_cfg["contract_path"])
    suite_contract_sha = _sha256_bytes(suite_contract_path.read_bytes())
    if suite_contract_sha != suite_cfg["expected_contract_sha256"]:
        raise AssertionError("Python reference suite contract hash drift")

    return catalog, catalog_file_sha, suite_contract_sha


def _family_report(repo: Path, suite_dir: Path, suite: dict[str, Any], family: str) -> dict[str, Any]:
    paths = (suite.get("artifact_paths") or {}).get(family)
    if not isinstance(paths, dict) or not paths.get("json"):
        raise AssertionError(f"reference suite missing family artifact: {family}")
    report_path = suite_dir / str(paths["json"])
    report = _read_json(report_path)
    if report.get("ok") is not True or report.get("family") != family:
        raise AssertionError(f"reference suite family report invalid: {family}")
    if report.get("exact_head") is not None and report.get("exact_head") != _head(repo):
        raise AssertionError(f"reference suite family exact-head drift: {family}")
    return report


def _route_list_from_report(report: dict[str, Any]) -> list[str] | None:
    routes = report.get("routes")
    if isinstance(routes, list) and all(isinstance(item, str) for item in routes):
        return list(routes)
    if isinstance(routes, dict):
        nested = routes.get("routes")
        if isinstance(nested, list) and all(isinstance(item, str) for item in nested):
            return list(nested)
    inventory = report.get("inventory")
    if isinstance(inventory, dict):
        nested = inventory.get("capability_routes")
        if isinstance(nested, list) and all(isinstance(item, str) for item in nested):
            return list(nested)
    return None


def certify(repo: Path) -> dict[str, Any]:
    contract, freeze_file_sha = _load_and_verify_freeze(repo)
    catalog, catalog_file_sha, suite_contract_sha = _verify_static_authorities(repo, contract)
    exact_head = _head(repo)
    if not exact_head:
        raise AssertionError("unable to resolve exact git HEAD")

    surface = public_surface.report()
    if surface.get("ok") is not True:
        raise AssertionError(f"canonical public surface is red: {surface}")
    python_surface = surface["python"]
    surface_cfg = contract["public_surface"]
    if python_surface["derived_count"] != int(surface_cfg["expected_route_count"]):
        raise AssertionError("canonical public route count drift")
    if python_surface["derived_sha256"] != surface_cfg["expected_route_sha256"]:
        raise AssertionError("canonical public route digest drift")

    execution = execution_contract.report()
    if execution.get("ok") is not True:
        raise AssertionError(f"Python public execution contract is red: {execution}")
    execution_cfg = contract["execution_contract"]
    python_execution = execution["python"]
    if execution.get("schema_version") != int(execution_cfg["expected_schema_version"]):
        raise AssertionError("Python public execution schema version drift")
    if python_execution["route_count"] != int(surface_cfg["expected_route_count"]):
        raise AssertionError("execution manifest route count drift")
    if python_execution["unique_execution_owner_count"] != int(execution_cfg["expected_unique_owner_count"]):
        raise AssertionError("execution owner count drift")
    for row in python_execution["manifest"]:
        if row.get("success_exit") != int(execution_cfg["expected_success_exit"]):
            raise AssertionError(f"public success exit drift: {row}")
        if row.get("parser_owned") and row.get("parser_error_exit") != int(execution_cfg["expected_parser_error_exit"]):
            raise AssertionError(f"public parser error exit drift: {row}")

    with tempfile.TemporaryDirectory(prefix="syntavra-python-behavior-freeze-") as temp_name:
        suite_dir = Path(temp_name) / "reference-suite"
        suite = run_suite(repo, suite_dir)
        suite_cfg = contract["reference_suite"]
        if suite.get("ok") is not True:
            raise AssertionError(f"Python reference suite is red inside behavior freeze: {suite}")
        if suite["exact_head"] != exact_head:
            raise AssertionError("Python reference suite exact-head drift inside behavior freeze")
        if suite["total"] != int(suite_cfg["expected_total_certifiers"]):
            raise AssertionError("Python reference suite certifier count drift")
        if suite["failed"] != int(suite_cfg["expected_failed"]):
            raise AssertionError("Python reference suite failure count drift")
        if suite["skipped"] != int(suite_cfg["expected_skipped"]):
            raise AssertionError("Python reference suite skipped count drift")
        if suite_cfg.get("must_preserve_clean_tree") and not (
            suite.get("repository_status_preserved")
            and suite.get("repository_clean_before")
            and suite.get("repository_clean_after")
        ):
            raise AssertionError("Python reference suite did not preserve a clean repository")
        if suite_cfg.get("must_be_offline") and "closed sink" not in str(suite.get("offline_policy")):
            raise AssertionError("Python reference suite offline policy drift")
        if suite.get("catalog_semantic_sha256") != contract["fixture_catalog"]["expected_semantic_sha256"]:
            raise AssertionError("Python reference suite catalog hash drift")
        if suite.get("contract_sha256") != suite_contract_sha:
            raise AssertionError("Python reference suite self-reported contract hash drift")

        families = list(catalog.get("families") or [])
        family_schema_versions: list[dict[str, Any]] = []
        family_behavior_contracts: list[dict[str, Any]] = []
        family_error_exit_policy: list[dict[str, Any]] = []
        nondeterminism_policy: list[dict[str, Any]] = []
        side_effect_expectations: list[dict[str, Any]] = []

        for row in families:
            section = str(row["section"])
            family = str(row["family"])
            report = _family_report(repo, suite_dir, suite, family)
            schema_version = report.get("schema_version")
            if not isinstance(schema_version, int) or schema_version < 1:
                raise AssertionError(f"invalid family schema version: {family} -> {schema_version!r}")

            static_contract = row.get("contract")
            static_contract_schema = None
            if static_contract is not None:
                static_value = _read_json(repo / str(static_contract))
                static_contract_schema = static_value.get("schema_version")
                if not isinstance(static_contract_schema, int) or static_contract_schema < 1:
                    raise AssertionError(f"invalid static family contract schema: {family}")

            family_schema_versions.append(
                {
                    "section": section,
                    "family": family,
                    "reference_schema_version": schema_version,
                    "static_contract": static_contract,
                    "static_contract_schema_version": static_contract_schema,
                }
            )

            fixtures = row.get("fixtures")
            snapshot = row.get("snapshot")
            nondeterministic = row.get("nondeterministic_fields")
            if not isinstance(fixtures, dict) or not isinstance(snapshot, dict) or not isinstance(nondeterministic, list):
                raise AssertionError(f"incomplete Q behavior policy for family: {family}")

            family_behavior_contracts.append(
                {
                    "section": section,
                    "family": family,
                    "schema_version": schema_version,
                    "routes": _route_list_from_report(report),
                    "exit_policy": report.get("exit_policy"),
                    "fixtures": fixtures,
                    "snapshot": snapshot,
                    "nondeterministic_fields": nondeterministic,
                    "static_contract": static_contract,
                }
            )
            family_error_exit_policy.append(
                {
                    "section": section,
                    "family": family,
                    "exit_policy": report.get("exit_policy"),
                    "malformed_input": fixtures.get("malformed_input"),
                    "domain_error": fixtures.get("domain_error"),
                }
            )
            nondeterminism_policy.append(
                {
                    "section": section,
                    "family": family,
                    "fields": nondeterministic,
                }
            )
            side_effect_expectations.append(
                {
                    "section": section,
                    "family": family,
                    "side_effect": fixtures.get("side_effect"),
                }
            )

        route_exit_defaults = [
            {
                "route": row["route"],
                "success_exit": row["success_exit"],
                "parser_error_exit": row["parser_error_exit"],
                "entrypoint": row["entrypoint"],
            }
            for row in python_execution["manifest"]
        ]
        error_exit_policy = {
            "route_defaults": route_exit_defaults,
            "families": family_error_exit_policy,
        }

        family_schema_sha = _semantic_sha(family_schema_versions)
        behavior_policy_sha = _semantic_sha(family_behavior_contracts)
        error_exit_policy_sha = _semantic_sha(error_exit_policy)
        nondeterminism_sha = _semantic_sha(nondeterminism_policy)
        side_effect_sha = _semantic_sha(side_effect_expectations)

        _expected_hash(contract, "expected_family_schema_sha256", family_schema_sha)
        _expected_hash(contract, "expected_behavior_policy_sha256", behavior_policy_sha)
        _expected_hash(contract, "expected_error_exit_policy_sha256", error_exit_policy_sha)
        _expected_hash(contract, "expected_nondeterminism_sha256", nondeterminism_sha)
        _expected_hash(contract, "expected_side_effect_sha256", side_effect_sha)

    return {
        "ok": True,
        "schema_version": 1,
        "family": "python-behavior-freeze",
        "engine": "python-reference-metadata",
        "phase": "S",
        "claim": contract["claim"],
        "strict": bool(contract["strict"]),
        "exact_head": exact_head,
        "freeze_contract": str(FREEZE_RELATIVE).replace("\\", "/"),
        "freeze_contract_sha256": freeze_file_sha,
        "fixture_catalog_sha256": catalog_file_sha,
        "fixture_catalog_semantic_sha256": _semantic_sha(catalog),
        "reference_suite_contract_sha256": suite_contract_sha,
        "public_surface": {
            "route_count": python_surface["derived_count"],
            "route_sha256": python_surface["derived_sha256"],
            "manifest": python_surface["manifest"],
            "duplicate_route_count": python_surface["duplicate_route_count"],
            "namespace_collision_count": python_surface["namespace_collision_count"],
        },
        "execution_contract": {
            "schema_version": execution["schema_version"],
            "unique_execution_owner_count": python_execution["unique_execution_owner_count"],
            "manifest": python_execution["manifest"],
            "parser_error_contract": python_execution["parser_error_contract"],
        },
        "reference_suite": {
            "total": suite["total"],
            "passed": suite["passed"],
            "failed": suite["failed"],
            "skipped": suite["skipped"],
            "repository_status_preserved": suite["repository_status_preserved"],
            "repository_clean_before": suite["repository_clean_before"],
            "repository_clean_after": suite["repository_clean_after"],
            "offline_policy": suite["offline_policy"],
        },
        "family_schema_versions": family_schema_versions,
        "family_behavior_contracts": family_behavior_contracts,
        "error_exit_policy": error_exit_policy,
        "nondeterminism_policy": nondeterminism_policy,
        "side_effect_expectations": side_effect_expectations,
        "derived_hashes": {
            "family_schema_sha256": family_schema_sha,
            "behavior_policy_sha256": behavior_policy_sha,
            "error_exit_policy_sha256": error_exit_policy_sha,
            "nondeterminism_sha256": nondeterminism_sha,
            "side_effect_sha256": side_effect_sha,
        },
        "route_authority_duplicated": False,
        "rust_python_required_for_fixture_consumption": False,
        "rust_native_promotion_credit": False,
        "frozen_rust_native_count": int(contract["policy"]["frozen_rust_native_count"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the final canonical Python Phase 1 behavior freeze")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "python-behavior-freeze",
            "engine": "python-reference-metadata",
            "phase": "S",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
