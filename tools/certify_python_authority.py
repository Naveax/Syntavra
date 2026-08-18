#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import traceback
from pathlib import Path
from typing import Any

from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

CONTRACT_RELATIVE = Path("contracts/python/python-authority-v1.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


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


def _assert_no_route_identity_copy(value: Any) -> None:
    if isinstance(value, list):
        if len(value) in {71, 245} and all(isinstance(item, str) for item in value):
            raise AssertionError(
                f"Python authority contract must not duplicate a {len(value)}-route identity list"
            )
        for item in value:
            _assert_no_route_identity_copy(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_route_identity_copy(item)


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def certify(repo: Path) -> dict[str, Any]:
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Python authority schema drift")
    _require(contract.get("family") == "python-authority", "Python authority family drift")
    _require(contract.get("phase") == "python-first", "Python authority phase drift")
    _require(contract.get("claim") == "PYTHON_FEATURE_DEVELOPMENT_AUTHORITY", "Python authority claim drift")
    _require(contract.get("strict") is True, "Python authority must remain strict")
    _assert_no_route_identity_copy(contract)

    authority = contract.get("authority") or {}
    expected = contract.get("expected") or {}
    rust_freeze = contract.get("rust_freeze") or {}
    policy = contract.get("policy") or {}

    expected_authorities = {
        "product_behavior_engine": "python",
        "feature_development_engine": "python",
        "route_identity": "tools/report_missing_native_public_routes.py",
        "behavior_freeze": "contracts/python/python-behavior-freeze-v1.json",
        "phase1_acceptance": "contracts/python/python-phase1-acceptance-v1.json",
        "implementation_surface": "contracts/engine/dual-engine-public-surface-v2.json",
        "promotion_baseline": "contracts/engine/phase2-rust-migration-matrix-v1.json",
    }
    _require(authority == expected_authorities, f"Python authority source map drift: {authority!r}")

    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact git HEAD")

    surface = public_surface.report()
    _require(surface.get("ok") is True, f"canonical public surface is red: {surface}")
    py_surface = surface.get("python") or {}
    rust_surface = surface.get("rust") or {}
    _require(int(py_surface.get("derived_count", -1)) == int(expected["public_routes"]), "Python public route count drift")
    _require(int(rust_surface.get("native_count", -1)) == int(expected["rust_promoted_native_routes"]), "Rust promoted-native count drift")
    _require(int(rust_surface.get("missing_count", -1)) == int(expected["remaining_routes"]), "Rust remaining-route count drift")

    execution = execution_contract.report()
    _require(execution.get("ok") is True, f"Python public execution contract is red: {execution}")
    py_execution = execution.get("python") or {}
    _require(int(py_execution.get("route_count", -1)) == int(expected["public_routes"]), "Python execution route count drift")
    _require(
        int(py_execution.get("unique_execution_owner_count", -1)) == int(expected["python_execution_owners"]),
        "Python execution owner count drift",
    )

    behavior = _read_json(repo / authority["behavior_freeze"])
    _require(behavior.get("strict") is True, "Python behavior freeze is not strict")
    _require(behavior.get("claim") == "PYTHON_REFERENCE_BEHAVIOR_FROZEN", "Python behavior freeze claim drift")
    behavior_policy = behavior.get("policy") or {}
    _require(behavior_policy.get("rust_native_promotion_credit") is False, "Python behavior freeze grants Rust promotion credit")
    _require(
        int(behavior_policy.get("frozen_rust_native_count", -1)) == int(expected["rust_promoted_native_routes"]),
        "Python behavior freeze Rust baseline drift",
    )

    phase1 = _read_json(repo / authority["phase1_acceptance"])
    _require(phase1.get("strict") is True, "Python Phase 1 acceptance is not strict")
    _require(phase1.get("claim") == "PYTHON_PHASE1_ACCEPTED", "Python Phase 1 acceptance claim drift")
    phase1_policy = phase1.get("policy") or {}
    _require(phase1_policy.get("rust_native_promotion_credit") is False, "Python Phase 1 grants Rust promotion credit")
    _require(
        int(phase1_policy.get("frozen_rust_native_count", -1)) == int(expected["rust_promoted_native_routes"]),
        "Python Phase 1 Rust baseline drift",
    )

    dual_surface = _read_json(repo / authority["implementation_surface"])
    _require(int((dual_surface.get("python_surface") or {}).get("public_command_count", -1)) == int(expected["public_routes"]), "Dual surface Python count drift")
    dual_rust = dual_surface.get("rust_surface") or {}
    _require(
        int(dual_rust.get("native_public_command_count", -1)) == int(expected["rust_implemented_native_routes"]),
        "Rust implementation coverage drift",
    )
    _require(int(dual_rust.get("python_launcher_bridge_command_count", -1)) == 0, "Rust launcher bridge reintroduced")
    dual_policy = dual_surface.get("policy") or {}
    _require(dual_policy.get("python_must_remain_independently_runnable") is True, "Python independent runtime policy drift")
    _require(dual_policy.get("rust_must_not_invoke_python") is True, "Rust-to-Python invocation policy drift")
    _require(dual_policy.get("hidden_fallback_forbidden") is True, "hidden fallback policy drift")

    migration = _read_json(repo / authority["promotion_baseline"])
    _require(migration.get("strict") is True, "Phase 2 migration baseline is not strict")
    baseline = migration.get("rust_baseline") or {}
    _require(int(baseline.get("expected_promoted_native", -1)) == int(expected["rust_promoted_native_routes"]), "migration promoted-native baseline drift")
    _require(int(baseline.get("expected_remaining", -1)) == int(expected["remaining_routes"]), "migration remaining baseline drift")
    _require(int(baseline.get("expected_remaining_owned", -1)) == int(expected["remaining_owned_routes"]), "migration owned-remaining baseline drift")
    _require(int(baseline.get("expected_unowned", -1)) == int(expected["unowned_routes"]), "migration unowned baseline drift")
    _require(int(baseline.get("atomic_promotion_target", -1)) == int(expected["atomic_promotion_target"]), "atomic promotion target drift")
    migration_policy = migration.get("policy") or {}
    _require(migration_policy.get("no_native_counter_change_in_this_gate") is True, "migration gate allows native counter change")
    _require(migration_policy.get("selector_ownership_is_not_behavioral_parity") is True, "selector ownership/parity boundary drift")

    _require(rust_freeze.get("active") is True, "Rust feature freeze is not active")
    _require(rust_freeze.get("feature_development_allowed") is False, "Rust feature development unexpectedly enabled")
    _require(rust_freeze.get("production_promotion_allowed") is False, "Rust production promotion unexpectedly enabled")
    _require(rust_freeze.get("native_counter_change_allowed") is False, "Rust native counter change unexpectedly enabled")
    _require(
        rust_freeze.get("allowed_maintenance") == ["build-blocker", "security", "data-loss", "contract-blocker"],
        "Rust maintenance exception set drift",
    )
    _require(rust_freeze.get("resume_claim") == "PYTHON_COMPLETE", "Rust resume claim drift")

    required_true_policies = [
        "route_identity_authority_single_source",
        "duplicate_route_list_forbidden",
        "native_implementation_is_not_production_promotion",
        "selector_ownership_is_not_behavioral_parity",
        "old_family_certifications_are_not_final_head_certifications",
        "python_must_remain_independently_runnable",
        "rust_must_not_invoke_python",
        "hidden_fallback_forbidden",
        "no_silent_fallback",
    ]
    for name in required_true_policies:
        _require(policy.get(name) is True, f"Python authority policy disabled: {name}")

    return {
        "ok": True,
        "schema_version": 1,
        "family": "python-authority",
        "phase": "python-first",
        "claim": contract["claim"],
        "exact_head": exact_head,
        "authority": authority,
        "python": {
            "feature_development_authority": True,
            "product_behavior_authority": True,
            "public_route_count": int(expected["public_routes"]),
            "execution_owner_count": int(expected["python_execution_owners"]),
        },
        "rust": {
            "feature_development_frozen": True,
            "implemented_native_routes": int(expected["rust_implemented_native_routes"]),
            "production_promoted_routes": int(expected["rust_promoted_native_routes"]),
            "remaining_routes": int(expected["remaining_routes"]),
            "remaining_owned_routes": int(expected["remaining_owned_routes"]),
            "unowned_routes": int(expected["unowned_routes"]),
            "atomic_promotion_target": int(expected["atomic_promotion_target"]),
            "resume_allowed": False,
            "resume_requires": rust_freeze["resume_requires"],
            "resume_claim": rust_freeze["resume_claim"],
        },
        "claim_boundary": (
            "This certificate establishes Python-first feature-development authority and freezes Rust production promotion at 174/245. "
            "It does not claim Python COMPLETE, does not grant Rust promotion credit, and does not certify Remaining-71 behavioral parity."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Python-first development authority.")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--out", help="Optional JSON output path")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    try:
        report = certify(repo)
    except Exception as exc:  # pragma: no cover - CLI failure envelope
        report = {
            "ok": False,
            "schema_version": 1,
            "family": "python-authority",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
