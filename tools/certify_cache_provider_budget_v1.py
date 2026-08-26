#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.python_phase_state import validate_python_complete_state

from syntavra_runtime.cache_provider_budget import CacheProviderBudgetEngine, ProviderBudgetPolicy
from syntavra_runtime.prompt_cache_optimizer import PromptCacheOptimizer
from syntavra_runtime.provider_account_pool import ProviderAccountPool

CONTRACT = Path("contracts/python/cache-provider-budget-v1.json")
WORKFLOW = Path(".github/workflows/cache-provider-budget.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    import subprocess
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "cache/provider certifier must run against its own checkout")
    contract = _json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "cache/provider contract schema drift")
    _require(contract.get("family") == "cache-provider-budget", "cache/provider contract family drift")
    _require(contract.get("claim") == "CACHE_PROVIDER_BUDGET_V1", "cache/provider claim drift")
    _require(contract.get("phase") == "python-first", "cache/provider phase drift")
    _require(contract.get("strict") is True, "cache/provider contract must remain strict")

    ownership = contract.get("ownership_policy") or {}
    for key in (
        "parallel_persistent_store_forbidden",
        "credential_ownership_forbidden",
        "provider_transport_ownership_forbidden",
        "existing_cache_store_reused",
        "existing_account_pool_reused",
        "existing_router_reused",
        "existing_capability_registry_reused",
    ):
        _require(ownership.get(key) is True, f"ownership policy disabled: {key}")
    _require(ownership.get("public_cli_route_added") is False, "cache/provider milestone may not add public CLI")

    budget = contract.get("budget_policy") or {}
    for key in (
        "provider_aware_prompt_cache_compiler",
        "provider_capability_negotiation",
        "expected_request_budget",
        "quota_reserve_gate",
        "quality_floor_gate",
        "context_window_gate",
        "cache_roi",
        "cache_bust_attribution",
        "deterministic_fallback_policy",
        "content_addressed_decision_receipt",
        "fail_closed_when_no_candidate_satisfies_constraints",
        "no_silent_budget_overrun",
    ):
        _require(budget.get(key) is True, f"budget policy disabled: {key}")

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, REGISTRY):
        _require((repo / relative).is_file(), f"missing cache/provider enforcement surface: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    _require("tests.runtime.test_cache_provider_budget_v1" in workflow, "workflow lost cache/provider regression suite")
    _require("tools/certify_cache_provider_budget_v1.py" in workflow, "workflow lost cache/provider certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"workflow action pin drift: {pin}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_cache_provider_budget_v1" in release, "release gate lost cache/provider tests")
    _require("tools/certify_cache_provider_budget_v1.py" in release, "release gate lost cache/provider certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/cache-provider-budget.yml"' in pins, "pin policy lost cache/provider workflow")

    registry = _json(repo / REGISTRY)
    by_id = {item["id"]: item for item in registry["capabilities"]}
    _require(by_id["epistemic_safety_v1"]["state"] == "certified", "Epistemic Safety must be certified first")
    _require(by_id["cache_provider_budget_v1"]["state"] in {"implemented", "verified", "certified"}, "cache/provider registry state not advanced")
    validate_python_complete_state(registry)

    with tempfile.TemporaryDirectory(prefix="syntavra-cache-provider-cert-") as td:
        root = Path(td)
        pool = ProviderAccountPool(root / "accounts.sqlite3")
        pool.register("openai", "primary", credential_ref="env:OPENAI_API_KEY", priority=10)
        pool.register("anthropic", "economy", credential_ref="env:ANTHROPIC_API_KEY", priority=5)
        engine = CacheProviderBudgetEngine(account_pool=pool, cache_optimizer=PromptCacheOptimizer(root))
        rows = [
            {"provider": "openai", "model": "reasoner", "quality": 0.95, "max_complexity": "reasoning", "context_window": 200000, "input_cost_per_million": 12.0, "output_cost_per_million": 30.0},
            {"provider": "anthropic", "model": "economy", "quality": 0.82, "max_complexity": "reasoning", "context_window": 200000, "input_cost_per_million": 1.0, "output_cost_per_million": 2.0},
        ]
        messages = [{"role": "system", "content": "stable policy " * 500}, {"role": "user", "content": "task"}]
        decision = engine.plan(
            messages,
            task="security architecture root cause",
            model_rows=rows,
            output_tokens_estimate=1000,
            policy=ProviderBudgetPolicy(max_expected_cost_usd=0.08, expected_requests=6),
            now=1000,
        )
        _require(decision.provider == "anthropic", "budget filter smoke failed")
        _require(decision.expected_cost_usd <= 0.08, "budget cap was exceeded")
        _require(decision.expected_savings_usd > 0, "cache ROI smoke failed")
        _require(bool(decision.receipt_hash), "decision receipt missing")

    status = CacheProviderBudgetEngine.status()
    for key in (
        "provider_aware_prompt_cache_compiler",
        "provider_budget_engine",
        "cache_roi",
        "cache_bust_attribution",
        "provider_capability_negotiation",
        "deterministic_fallback_policy",
        "provider_account_pool_reused",
        "prompt_cache_optimizer_reused",
        "adaptive_provider_router_reused",
        "provider_gateway_capabilities_reused",
    ):
        _require(status.get(key) is True, f"runtime surface disabled: {key}")
    _require(status.get("parallel_persistent_store") is False, "cache/provider runtime introduced a store")
    _require(status.get("credential_ownership") is False, "cache/provider runtime claims credentials")
    _require(status.get("public_cli_route") is False, "cache/provider runtime claims public CLI")

    admission = contract.get("admission") or {}
    _require(admission.get("rust_production_promoted") == 174, "Rust promotion baseline drift")
    _require(admission.get("rust_remaining_parity_promotion") == 71, "Rust remaining baseline drift")
    exact_head = _head(repo)
    _require(len(exact_head) == 40, "unable to resolve exact head")
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "CACHE_PROVIDER_BUDGET_V1",
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": status,
        "rust": {"production_promoted": 174, "remaining_parity_promotion": 71, "feature_development_frozen": True},
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Cache/Provider Budget v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 1, "claim": "CACHE_PROVIDER_BUDGET_V1", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
