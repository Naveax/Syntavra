from __future__ import annotations

import argparse
from dataclasses import replace
import json
import subprocess
from pathlib import Path
from typing import Any

from syntavra_runtime.context_governor import (
    AuctionContext,
    AuctionCostEvent,
    LaneBudgetRequest,
    MarginalTokenBid,
    TokenBudgetAllocator,
    TokenBudgetAuctionDecision,
)
from syntavra_runtime.provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelopeCompiler,
    TokenEnvelopePolicy,
)


CONTRACT_RELATIVE = Path("contracts/python/u5-p0-05-global-token-budget-auction-v1.json")
PUBLIC_SURFACE_RELATIVE = Path("contracts/engine/dual-engine-public-surface-v2.json")
RUNTIME_RELATIVE = Path("syntavra_runtime/context_governor.py")
PROVIDER_ENVELOPE_RELATIVE = Path("syntavra_runtime/provider_token_envelope.py")
OUTPUT_GOVERNOR_RELATIVE = Path("syntavra_runtime/output_governor.py")
OBSERVABILITY_RELATIVE = Path("syntavra_runtime/observability_attribution.py")
TEST_RELATIVE = Path("tests/runtime/test_token_budget_allocator.py")
WORKFLOW_RELATIVE = Path(".github/workflows/u5-p0-05-global-token-budget-auction.yml")
DOC_RELATIVE = Path("docs/U5_P0_05_GLOBAL_TOKEN_BUDGET_AUCTION.md")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, encoding="utf-8"
    ).strip()


def _load_contract(repo: Path) -> dict[str, Any]:
    path = repo / CONTRACT_RELATIVE
    _require(path.is_file(), f"missing contract: {CONTRACT_RELATIVE}")
    data = json.loads(path.read_text(encoding="utf-8"))
    _require(data["mechanism_id"] == "U5-P0-05", "mechanism id drift")
    _require(data["wave"] == "TE-U33", "wave drift")
    _require(data["classification"] == "NEW_HARDEN", "classification drift")
    policy = data["allocation_policy"]
    _require(
        policy["provider_envelope_remains_hard_budget_and_admission_authority"] is True,
        "ProviderTokenEnvelope authority weakened",
    )
    _require(policy["fixed_lane_baseline_exactly_partitions_envelope"] is True, "fixed baseline weakened")
    _require(policy["hard_minima_seed_allocation_before_auction"] is True, "hard minima weakened")
    _require(policy["mandatory_envelope_tokens_may_not_be_auctioned_away"] is True, "mandatory minima weakened")
    _require(policy["shadow_or_offline_only"] is True, "shadow/offline promotion boundary weakened")
    _require(policy["live_reallocation_allowed"] is False, "live reallocation admitted early")
    bounds = data["authority_boundaries"]
    _require(bounds["allocator_owns_provider_admission"] is False, "parallel provider authority introduced")
    _require(bounds["allocator_owns_context_selection"] is False, "parallel context authority introduced")
    _require(bounds["allocator_owns_reasoning_policy"] is False, "parallel reasoning authority introduced")
    _require(bounds["allocator_owns_output_rendering"] is False, "parallel output authority introduced")
    _require(data["accounting"]["provider_savings_claim"] is False, "provider claim boundary weakened")
    return data


def _envelope():
    policy = TokenEnvelopePolicy(
        target_total_tokens=140,
        target_input_tokens=100,
        target_output_tokens=40,
        input_target_fraction=1.0,
        output_target_fraction=1.0,
        minimum_avoidable_reduction=0.0,
        minimum_input_budget_tokens=1,
        minimum_output_budget_tokens=1,
    )
    return ProviderTokenEnvelopeCompiler(policy).compile(
        original_input_tokens=100,
        original_output_budget_tokens=40,
        mandatory_input_tokens=30,
        mandatory_output_tokens=10,
        input_leases=(
            NecessityLease("user", "user_instruction", 20, "frozen:user"),
            NecessityLease(
                "verifier",
                "verifier_evidence",
                10,
                "frozen:verifier",
                exact_required=True,
            ),
        ),
        output_leases=(
            NecessityLease("requested", "requested_output", 10, "frozen:output"),
        ),
    )


def _context(mode: str = "shadow") -> AuctionContext:
    return AuctionContext(
        "openai",
        "gpt-test",
        "scoped-coding",
        "target-tokenizer-v1",
        "frozen:task-set-v1",
        "frozen:verifier-set-v1",
        mode,
    )


def _requests() -> tuple[LaneBudgetRequest, ...]:
    return (
        LaneBudgetRequest(
            "retrieval",
            35,
            10,
            60,
            "min:retrieval",
            (
                MarginalTokenBid("r1", "retrieval", 20, 8.0, "trace:r1", 0),
                MarginalTokenBid("r2", "retrieval", 30, 3.0, "trace:r2", 1),
            ),
        ),
        LaneBudgetRequest(
            "instructions_context",
            40,
            15,
            50,
            "min:instructions",
            (MarginalTokenBid("i1", "instructions_context", 20, 6.0, "trace:i1", 0),),
        ),
        LaneBudgetRequest(
            "schema_tool_output",
            25,
            5,
            30,
            "min:schema",
            (MarginalTokenBid("s1", "schema_tool_output", 20, 4.0, "trace:s1", 0),),
        ),
        LaneBudgetRequest(
            "reasoning",
            20,
            5,
            30,
            "min:reasoning",
            (MarginalTokenBid("q1", "reasoning", 25, 10.0, "trace:q1", 0),),
        ),
        LaneBudgetRequest(
            "output",
            20,
            5,
            30,
            "min:output",
            (MarginalTokenBid("o1", "output", 25, 5.0, "trace:o1", 0),),
        ),
    )


def _runtime_probe() -> dict[str, Any]:
    envelope = _envelope()
    allocator = TokenBudgetAllocator()
    status = allocator.status()
    _require(status["provider_envelope_remains_budget_authority"] is True, "envelope authority drift")
    _require(status["allocator_owns_provider_admission"] is False, "provider authority drift")
    _require(status["allocator_owns_context_selection"] is False, "context authority drift")
    _require(status["allocator_owns_reasoning_policy"] is False, "reasoning authority drift")
    _require(status["allocator_owns_output_rendering"] is False, "output authority drift")
    _require(status["shadow_or_offline_only"] is True, "shadow boundary drift")

    shadow = allocator.allocate(envelope=envelope, context=_context(), requests=_requests())
    _require(shadow.effective_allocation == shadow.fixed_allocation, "shadow mode changed effective allocation")
    _require(shadow.recommended_allocation != shadow.fixed_allocation, "auction did not produce a distinct recommendation")
    _require(
        sum(shadow.recommended_allocation[lane] for lane in ("retrieval", "instructions_context", "schema_tool_output"))
        == envelope.provider_input_budget_tokens,
        "recommended input allocation escaped envelope",
    )
    _require(
        sum(shadow.recommended_allocation[lane] for lane in ("reasoning", "output"))
        == envelope.provider_output_budget_tokens,
        "recommended output allocation escaped envelope",
    )
    _require(
        shadow.recommended_estimated_verified_gain + 1e-12 >= shadow.fixed_estimated_verified_gain,
        "recommendation lost estimated verified value",
    )
    _require(TokenBudgetAuctionDecision.verify_receipt(shadow.receipt()), "auction receipt verification failed")

    offline = TokenBudgetAllocator().allocate(
        envelope=envelope, context=_context("offline"), requests=_requests()
    )
    _require(offline.effective_allocation == offline.recommended_allocation, "offline recommendation not applied")

    live_rejected = False
    try:
        _context("live")
    except ValueError:
        live_rejected = True
    _require(live_rejected, "live reallocation was admitted")

    minima_rejected = False
    weakened = list(_requests())
    weakened[0] = replace(weakened[0], hard_min_tokens=0, hard_min_evidence_ref="")
    try:
        TokenBudgetAllocator().allocate(
            envelope=envelope, context=_context(), requests=weakened
        )
    except ValueError:
        minima_rejected = True
    _require(minima_rejected, "mandatory envelope tokens were auctioned away")

    envelope_rejection_preserved = False
    try:
        TokenBudgetAllocator().allocate(
            envelope=replace(envelope, provider_call_admissible=False),
            context=_context(),
            requests=_requests(),
        )
    except ValueError:
        envelope_rejection_preserved = True
    _require(envelope_rejection_preserved, "allocator bypassed ProviderTokenEnvelope rejection")

    for index, kind in enumerate(("retry", "recall", "repair", "fallback"), start=1):
        allocator.record_overhead(
            AuctionCostEvent(
                f"event-{index}",
                kind,
                "retrieval",
                index,
                f"trace:{kind}",
                "a" * 64,
                "b" * 64,
            )
        )
    accounting = allocator.accounting_receipt(
        shadow,
        baseline_usage_receipt_hash="c" * 64,
        baseline_token_receipt_hash="d" * 64,
        optimized_usage_receipt_hash="e" * 64,
        optimized_token_receipt_hash="f" * 64,
    )
    _require(accounting["overhead_provider_visible_tokens"] == 10, "end-to-end overhead accounting drift")
    _require(accounting["provider_verified_overhead_tokens"] == 10, "provider-verified overhead drift")
    _require(accounting["paired_execution_evidence_complete"] is True, "paired execution evidence drift")
    _require(accounting["provider_savings_claim"] is False, "provider savings claim escaped")
    _require(TokenBudgetAllocator.verify_accounting_receipt(accounting), "accounting receipt verification failed")

    return {
        "provider_envelope_authority_preserved": True,
        "five_lane_auction": tuple(shadow.fixed_allocation) == (
            "retrieval",
            "instructions_context",
            "schema_tool_output",
            "reasoning",
            "output",
        ),
        "hard_minima_preserved": minima_rejected,
        "fixed_baseline_exact_partition": True,
        "shadow_effective_is_fixed": shadow.effective_allocation == shadow.fixed_allocation,
        "offline_effective_is_recommended": offline.effective_allocation == offline.recommended_allocation,
        "live_reallocation_rejected": live_rejected,
        "envelope_rejection_preserved": envelope_rejection_preserved,
        "fixed_estimated_verified_gain": shadow.fixed_estimated_verified_gain,
        "recommended_estimated_verified_gain": shadow.recommended_estimated_verified_gain,
        "overhead_provider_visible_tokens": accounting["overhead_provider_visible_tokens"],
        "provider_verified_overhead_tokens": accounting["provider_verified_overhead_tokens"],
        "paired_execution_evidence_complete": accounting["paired_execution_evidence_complete"],
        "provider_savings_claim": False,
    }


def _validate_public_surface(repo: Path) -> dict[str, Any]:
    data = json.loads((repo / PUBLIC_SURFACE_RELATIVE).read_text(encoding="utf-8"))
    python_surface = data.get("python_surface")
    _require(isinstance(python_surface, dict), "python public surface missing")
    _require(python_surface.get("module_count") == 234, "U5-P0-05 must not add a runtime module")
    _require(python_surface.get("public_command_count") == 245, "U5-P0-05 must not change public command count")
    return {
        "module_count": python_surface["module_count"],
        "public_command_count": python_surface["public_command_count"],
        "command_paths_sha256": python_surface["command_paths_sha256"],
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (
        RUNTIME_RELATIVE,
        PROVIDER_ENVELOPE_RELATIVE,
        OUTPUT_GOVERNOR_RELATIVE,
        OBSERVABILITY_RELATIVE,
        TEST_RELATIVE,
        WORKFLOW_RELATIVE,
        DOC_RELATIVE,
    ):
        _require((repo / relative).is_file(), f"missing U5-P0-05 enforcement surface: {relative.as_posix()}")

    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require(
        "group: u5-p0-05-global-token-budget-auction-${{ github.event.pull_request.number || github.ref }}"
        in workflow,
        "workflow concurrency is not PR/ref scoped",
    )
    _require("tests.runtime.test_token_budget_allocator" in workflow, "workflow lost dedicated regressions")
    _require(
        "tools/validate_u5_p0_05_global_token_budget_auction.py" in workflow,
        "workflow lost exact-head certifier",
    )
    _require(
        "tools/verify_dual_engine_public_surface.py" in workflow,
        "workflow lost public-surface drift guard",
    )
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"action pin drift: {pin}")
    return {
        "runtime": RUNTIME_RELATIVE.as_posix(),
        "tests": TEST_RELATIVE.as_posix(),
        "workflow": WORKFLOW_RELATIVE.as_posix(),
        "doc": DOC_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    contract = _load_contract(repo)
    runtime = _runtime_probe()
    public_surface = _validate_public_surface(repo)
    enforcement = _validate_enforcement(repo)
    exact_head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all")
    _require(not dirty, "exact-head certification requires a clean repository")
    return {
        "ok": True,
        "claim": "U5_P0_05_GLOBAL_TOKEN_BUDGET_AUCTION",
        "mechanism_id": contract["mechanism_id"],
        "wave": contract["wave"],
        "exact_head": exact_head,
        "admission_ready": True,
        "runtime": runtime,
        "public_surface": public_surface,
        "enforcement": enforcement,
        "provider_savings_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = certify(args.repo.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
