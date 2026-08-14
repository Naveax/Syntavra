#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.util import canonical_json
from tools import report_python_public_dispatch_fallthrough as dispatch_audit
from tools import certify_python_behavior_freeze as behavior_freeze

CONTRACT_RELATIVE = Path("contracts/python/python-phase1-acceptance-v1.json")
CONTRACT_HASH_RELATIVE = Path("contracts/python/python-phase1-acceptance-v1.sha256")


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
            raise AssertionError("Phase 1 acceptance contract must not duplicate the canonical 245-route list")
        for item in value:
            _assert_no_full_route_copy(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_full_route_copy(item)


def _load_contract(repo: Path) -> tuple[dict[str, Any], str]:
    path = repo / CONTRACT_RELATIVE
    companion = repo / CONTRACT_HASH_RELATIVE
    contract = _read_json(path)
    if contract.get("schema_version") != 1 or contract.get("family") != "python-phase1-acceptance":
        raise AssertionError("Python Phase 1 acceptance contract identity drift")
    if contract.get("phase") != "final-python-phase1":
        raise AssertionError("Python Phase 1 acceptance phase drift")
    _assert_no_full_route_copy(contract)

    observed = _sha256_bytes(path.read_bytes())
    parts = companion.read_text(encoding="utf-8").strip().split()
    if not parts or len(parts[0]) != 64:
        raise AssertionError("invalid Python Phase 1 acceptance companion SHA-256 file")
    if observed != parts[0]:
        raise AssertionError(f"Phase 1 acceptance file hash drift: {observed} != {parts[0]}")
    return contract, observed


def _expected_hash(contract: dict[str, Any], observed: str) -> None:
    expected = (contract.get("derived_freeze") or {}).get("expected_route_semantics_sha256")
    strict = bool(contract.get("strict"))
    if expected is None:
        if strict:
            raise AssertionError("strict Phase 1 acceptance is missing route semantics hash")
        return
    if not isinstance(expected, str) or len(expected) != 64:
        raise AssertionError(f"invalid expected route semantics hash: {expected!r}")
    if observed != expected:
        raise AssertionError(f"route semantics drift: {observed} != {expected}")


def _collect_exit_values(value: Any) -> list[int]:
    found: set[int] = set()

    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, str(child_key))
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child, key)
        elif (
            isinstance(node, int)
            and not isinstance(node, bool)
            and "exit" in key.casefold()
            and -(2**31) <= node <= 2**31 - 1
        ):
            found.add(node)

    visit(value)
    return sorted(found)


def _family_index(freeze: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(freeze.get("family_behavior_contracts") or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "")
        if not family or family in result:
            raise AssertionError(f"invalid/duplicate frozen family behavior row: {row}")
        result[family] = row
    return result


def _family_error_index(freeze: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(((freeze.get("error_exit_policy") or {}).get("families")) or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "")
        if not family or family in result:
            raise AssertionError(f"invalid/duplicate frozen family error row: {row}")
        result[family] = row
    return result


def _family_side_effect_index(freeze: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(freeze.get("side_effect_expectations") or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "")
        if not family or family in result:
            raise AssertionError(f"invalid/duplicate frozen family side-effect row: {row}")
        result[family] = row
    return result


def _family_nondeterminism_index(freeze: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(freeze.get("nondeterminism_policy") or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "")
        if not family or family in result:
            raise AssertionError(f"invalid/duplicate frozen family nondeterminism row: {row}")
        result[family] = row
    return result


def _family_policy_rows(
    freeze: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    families = _family_index(freeze)
    errors = _family_error_index(freeze)
    side_effects = _family_side_effect_index(freeze)
    nondeterminism = _family_nondeterminism_index(freeze)
    if not (set(families) == set(errors) == set(side_effects) == set(nondeterminism)):
        raise AssertionError("frozen family policy indexes disagree")

    policies: dict[str, dict[str, Any]] = {}
    t_meta: dict[str, dict[str, Any]] = {}
    for family, row in families.items():
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        projection = snapshot.get("projection") if isinstance(snapshot.get("projection"), dict) else {}
        error_row = errors[family]
        side_row = side_effects[family]
        nondet_row = nondeterminism[family]
        domain_codes = sorted(
            set(
                _collect_exit_values(row.get("exit_policy"))
                + _collect_exit_values(error_row.get("domain_error"))
            )
        )
        policy = {
            "family": family,
            "section": row.get("section"),
            "schema_version": row.get("schema_version"),
            "snapshot_sha256": snapshot.get("sha256"),
            "static_contract": row.get("static_contract"),
            "exit_policy": row.get("exit_policy"),
            "domain_error_fixture": error_row.get("domain_error"),
            "malformed_input_fixture": error_row.get("malformed_input"),
            "side_effect_fixture": side_row.get("side_effect"),
            "nondeterministic_fields": nondet_row.get("fields"),
            "defined_domain_application_exit_codes": domain_codes,
        }
        if family == contract["route_binding"]["core_legacy_family"]:
            policy["route_contract_sha256"] = projection.get("route_contract_sha256")
            policy["route_side_effect_sha256"] = projection.get("side_effect_sha256")
            policy["route_idempotency_sha256"] = projection.get("idempotency_sha256")
            for key in (
                "route_contract_sha256",
                "route_side_effect_sha256",
                "route_idempotency_sha256",
            ):
                if not isinstance(policy.get(key), str) or len(str(policy[key])) != 64:
                    raise AssertionError(f"T route policy hash missing: {key}")
            t_meta[family] = policy
        policies[family] = policy

    expected_family_count = int(contract["behavior_freeze"]["expected_family_count"])
    if len(policies) != expected_family_count:
        raise AssertionError(f"frozen family count drift: {len(policies)} != {expected_family_count}")
    return policies, t_meta


def _bind_routes(
    freeze: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    route_cfg = contract["route_binding"]
    execution_rows = list((freeze.get("execution_contract") or {}).get("manifest") or [])
    by_route = {str(row.get("route")): row for row in execution_rows}
    if len(by_route) != int(route_cfg["expected_route_count"]):
        raise AssertionError("execution route manifest count drift")

    families = _family_index(freeze)
    owners: dict[str, str] = {}
    overlaps: list[dict[str, Any]] = []
    for family, row in families.items():
        routes = row.get("routes")
        if routes is None:
            continue
        if not isinstance(routes, list) or any(not isinstance(route, str) for route in routes):
            raise AssertionError(f"invalid frozen family route list: {family}")
        for route in routes:
            if route not in by_route:
                raise AssertionError(f"family {family} references non-canonical route: {route}")
            if route in owners and owners[route] != family:
                overlaps.append({"route": route, "families": sorted({owners[route], family})})
                continue
            owners[route] = family

    explicit_count = len(owners)
    if explicit_count != int(route_cfg["expected_family_explicit_route_count"]):
        raise AssertionError(
            f"explicit family route count drift: {explicit_count} != "
            f"{route_cfg['expected_family_explicit_route_count']}"
        )
    if overlaps and route_cfg.get("overlapping_primary_family_routes_forbidden"):
        raise AssertionError(f"overlapping primary family routes: {overlaps}")

    canonical = sorted(by_route)
    unbound = sorted(set(canonical) - set(owners))
    t_family = str(route_cfg["core_legacy_family"])
    if len(unbound) != int(route_cfg["expected_core_legacy_route_count"]):
        raise AssertionError(
            f"core/legacy route complement drift: {len(unbound)} != "
            f"{route_cfg['expected_core_legacy_route_count']}"
        )
    if t_family not in families:
        raise AssertionError("core/legacy frozen family is missing")
    if families[t_family].get("routes") is not None:
        raise AssertionError("core/legacy family must remain complement-derived, not a duplicated route list")
    for route in unbound:
        owners[route] = t_family

    still_unbound = sorted(set(canonical) - set(owners))
    if still_unbound and route_cfg.get("unbound_routes_forbidden"):
        raise AssertionError(f"unbound canonical routes: {still_unbound}")

    return owners, execution_rows, still_unbound, overlaps


def _route_semantics_rows(
    freeze: dict[str, Any],
    contract: dict[str, Any],
    family_policies: dict[str, dict[str, Any]],
    owners: dict[str, str],
    execution_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    required = list(contract.get("required_route_semantics") or [])
    default_app_exit = int(contract["policy"]["public_application_failure_default_exit"])
    unknown_public_exit = int(contract["policy"]["unknown_public_selector_exit"])
    t_family = str(contract["route_binding"]["core_legacy_family"])
    rows: list[dict[str, Any]] = []

    for execution in sorted(execution_rows, key=lambda item: str(item["route"])):
        route = str(execution["route"])
        family = owners[route]
        family_policy = family_policies[family]
        parser_owned = bool(execution.get("parser_owned"))
        parser_exit = execution.get("parser_error_exit")
        if parser_owned and parser_exit != unknown_public_exit:
            raise AssertionError(f"parser error exit drift for {route}: {parser_exit}")
        argument_policy = {
            "mode": "direct-parser-leaf" if parser_owned else "inherited-selector-parser",
            "exit": parser_exit if parser_exit is not None else unknown_public_exit,
            "source": "S.execution_contract.manifest",
        }

        domain_codes = list(family_policy["defined_domain_application_exit_codes"])
        if default_app_exit not in domain_codes:
            domain_codes.append(default_app_exit)
        domain_codes = sorted(set(domain_codes))

        if family == t_family:
            stdout_policy = {
                "mode": "content-addressed-per-route-minimum-execution",
                "route_contract_sha256": family_policy["route_contract_sha256"],
                "guarantee": "stdout format and top-level JSON key shape are frozen by the strict T route matrix; raw dynamic values are not implicitly frozen",
            }
            stderr_policy = {
                "mode": "content-addressed-per-route-minimum-execution",
                "route_contract_sha256": family_policy["route_contract_sha256"],
                "guarantee": "stderr format and traceback absence are frozen by the strict T route matrix",
            }
            ordering = {
                "mode": "explicit-no-extra-ordering-guarantee",
                "source": family_policy["route_contract_sha256"],
                "guarantee": "only ordering represented by the T content-addressed route projection is contractual; no stronger raw text/list ordering is implicit",
            }
            idempotency = {
                "mode": "repeat-shape-and-filesystem-delta-content-addressed",
                "sha256": family_policy["route_idempotency_sha256"],
                "no_implicit_guarantee": True,
            }
            side_effect_policy = {
                "policy_id": f"{family}.side-effect",
                "route_side_effect_sha256": family_policy["route_side_effect_sha256"],
            }
        else:
            stdout_policy = {
                "mode": "family-contract-defined",
                "policy_id": f"{family}.output",
                "snapshot_sha256": family_policy["snapshot_sha256"],
                "no_implicit_format_beyond_family_contract": True,
            }
            stderr_policy = {
                "mode": "family-contract-defined",
                "policy_id": f"{family}.output",
                "snapshot_sha256": family_policy["snapshot_sha256"],
                "no_implicit_format_beyond_family_contract": True,
            }
            ordering = {
                "mode": "family-snapshot-defined",
                "snapshot_sha256": family_policy["snapshot_sha256"],
                "no_implicit_ordering": True,
            }
            idempotency = {
                "mode": "family-defined-only",
                "snapshot_sha256": family_policy["snapshot_sha256"],
                "no_implicit_guarantee": True,
            }
            side_effect_policy = {"policy_id": f"{family}.side-effect"}

        semantics = {
            "success_exit_code": {
                "exit": execution.get("success_exit"),
                "source": "S.execution_contract.manifest",
            },
            "domain_application_error_policy": {
                "policy_id": f"{family}.error",
                "defined_exit_codes": domain_codes,
                "public_application_failure_default_exit": default_app_exit,
                "no_unlisted_family_guarantee": True,
            },
            "argument_parser_error_policy": argument_policy,
            "stdout_format_policy": stdout_policy,
            "stderr_format_policy": stderr_policy,
            "json_envelope_schema_policy": {
                "mode": "family-contract-where-applicable",
                "policy_id": f"{family}.output",
                "malformed_policy_id": f"{family}.malformed",
                "domain_policy_id": f"{family}.domain",
                "non_json_output_allowed_only_when_family_contract_defines_it": True,
            },
            "ordering_guarantee": ordering,
            "filesystem_state_side_effect_policy": side_effect_policy,
            "idempotency_behavior": idempotency,
            "missing_input_behavior": {
                "required_parser_input": {
                    "exit": unknown_public_exit,
                    "format": "argparse-usage-error",
                },
                "optional_or_state_missing": f"{family}.domain",
                "no_implicit_missing_state_success": True,
            },
            "malformed_input_behavior": {
                "policy_id": f"{family}.malformed",
                "fixture": family_policy["malformed_input_fixture"],
            },
            "unsupported_operation_behavior": {
                "unknown_public_command_or_subcommand_exit": unknown_public_exit,
                "parser_valid_route_owner_count": int(contract["policy"]["parser_valid_route_owner_count"]),
                "generic_runtime_fallthrough_reachable": bool(
                    contract["policy"]["generic_runtime_fallthrough_reachable"]
                ),
                "parser_valid_unsupported_selector": f"{family}.domain",
            },
        }

        missing = [
            field for field in required
            if field not in semantics or semantics[field] in (None, "", [], {})
        ]
        if missing:
            raise AssertionError(f"route semantics incomplete for {route}: {missing}")
        rows.append(
            {
                "route": route,
                "family": family,
                "sources": execution.get("sources"),
                "entrypoint": execution.get("entrypoint"),
                "parser_owned": parser_owned,
                **semantics,
            }
        )

    return rows


def certify(repo: Path) -> dict[str, Any]:
    contract, contract_sha = _load_contract(repo)
    exact_head = _head(repo)
    if not exact_head:
        raise AssertionError("unable to resolve exact git HEAD")

    freeze_path = repo / str(contract["behavior_freeze"]["path"])
    observed_freeze_sha = _sha256_bytes(freeze_path.read_bytes())
    if observed_freeze_sha != contract["behavior_freeze"]["expected_sha256"]:
        raise AssertionError("strict S behavior freeze file hash drift")

    freeze = behavior_freeze.certify(repo)
    freeze_cfg = contract["behavior_freeze"]
    if freeze.get("ok") is not True or freeze.get("strict") is not True:
        raise AssertionError("strict S behavior freeze is not green")
    if freeze.get("claim") != freeze_cfg["expected_claim"]:
        raise AssertionError("strict S behavior freeze claim drift")
    if freeze.get("exact_head") != exact_head:
        raise AssertionError("strict S behavior freeze exact-head drift")
    if len(freeze.get("family_schema_versions") or []) != int(freeze_cfg["expected_family_count"]):
        raise AssertionError("strict S family count drift")
    suite = freeze.get("reference_suite") or {}
    if suite.get("total") != int(freeze_cfg["expected_suite_certifiers"]) or suite.get("passed") != suite.get("total"):
        raise AssertionError("strict S embedded suite count/status drift")
    if suite.get("failed") != 0 or suite.get("skipped") != 0:
        raise AssertionError("strict S embedded suite is not fully green")
    if not (
        suite.get("repository_status_preserved")
        and suite.get("repository_clean_before")
        and suite.get("repository_clean_after")
    ):
        raise AssertionError("strict S embedded suite did not preserve clean repository state")

    dispatch = dispatch_audit.report()
    if dispatch.get("ok") is not True:
        raise AssertionError(f"public dispatch/fallthrough audit is red: {dispatch}")

    family_policies, _ = _family_policy_rows(freeze, contract)
    owners, execution_rows, unbound, overlaps = _bind_routes(freeze, contract)
    route_rows = _route_semantics_rows(
        freeze,
        contract,
        family_policies,
        owners,
        execution_rows,
    )
    route_cfg = contract["route_binding"]
    if len(route_rows) != int(route_cfg["expected_route_count"]):
        raise AssertionError("route semantics row count drift")

    required = list(contract.get("required_route_semantics") or [])
    if len(required) != 12 or len(set(required)) != len(required):
        raise AssertionError("required route semantics vocabulary drift")

    semantics_sha = _semantic_sha(route_rows)
    _expected_hash(contract, semantics_sha)

    family_counts: dict[str, int] = {}
    for row in route_rows:
        family = str(row["family"])
        family_counts[family] = family_counts.get(family, 0) + 1

    t_family = str(route_cfg["core_legacy_family"])
    explicit_count = len(route_rows) - family_counts.get(t_family, 0)
    if explicit_count != int(route_cfg["expected_family_explicit_route_count"]):
        raise AssertionError("final explicit family route count drift")
    if family_counts.get(t_family, 0) != int(route_cfg["expected_core_legacy_route_count"]):
        raise AssertionError("final core/legacy route count drift")

    return {
        "ok": True,
        "schema_version": 1,
        "family": "python-phase1-acceptance",
        "phase": "final-python-phase1",
        "claim": contract["claim"],
        "strict": bool(contract["strict"]),
        "engine": "python-reference-metadata",
        "exact_head": exact_head,
        "acceptance_contract": str(CONTRACT_RELATIVE).replace("\\", "/"),
        "acceptance_contract_sha256": contract_sha,
        "behavior_freeze_sha256": observed_freeze_sha,
        "behavior_freeze": {
            "claim": freeze["claim"],
            "strict": freeze["strict"],
            "route_count": freeze["public_surface"]["route_count"],
            "family_count": len(freeze["family_schema_versions"]),
            "suite_total": suite["total"],
            "suite_passed": suite["passed"],
            "suite_failed": suite["failed"],
            "suite_skipped": suite["skipped"],
            "repository_status_preserved": suite["repository_status_preserved"],
            "repository_clean_before": suite["repository_clean_before"],
            "repository_clean_after": suite["repository_clean_after"],
            "offline_policy": suite["offline_policy"],
            "derived_hashes": freeze["derived_hashes"],
        },
        "dispatch_contract": {
            "ok": True,
            "schema_version": dispatch.get("schema_version"),
            "handler_failure_count": (dispatch.get("python") or {}).get("handler_failure_count"),
            "dispatcher_failure_count": (dispatch.get("python") or {}).get("dispatcher_failure_count"),
            "generic_runtime_fallthrough_reachable": bool(
                contract["policy"]["generic_runtime_fallthrough_reachable"]
            ),
        },
        "route_binding": {
            "route_count": len(route_rows),
            "family_explicit_route_count": explicit_count,
            "core_legacy_route_count": family_counts.get(t_family, 0),
            "unbound_route_count": len(unbound),
            "overlap_count": len(overlaps),
            "family_counts": dict(sorted(family_counts.items())),
        },
        "required_route_semantics": required,
        "route_semantics_complete": True,
        "route_semantics_sha256": semantics_sha,
        "family_policies": [
            family_policies[key]
            for key in sorted(family_policies)
        ],
        "route_semantics": route_rows,
        "rust_native_promotion_credit": False,
        "frozen_rust_native_count": 174,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify final Python Phase 1 route-level acceptance")
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
            "family": "python-phase1-acceptance",
            "phase": "final-python-phase1",
            "engine": "python-reference-metadata",
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
