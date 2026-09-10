from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
)
from syntavra_runtime.context_decision_trace import ContextDecisionTrace
from syntavra_runtime.context_governor import ContextCostEstimate, ReacquisitionTaxGovernor


CONTRACT_RELATIVE = Path("contracts/python/u5-p0-02-reacquisition-tax-governor-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/u5-p0-02-reacquisition-tax-governor.yml")
TEST_RELATIVE = Path("tests/runtime/test_reacquisition_tax_governor.py")
RUNTIME_RELATIVE = Path("syntavra_runtime/context_governor.py")
DOC_RELATIVE = Path("docs/U5_P0_02_REACQUISITION_TAX_GOVERNOR.md")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8").strip()


def _load_contract(repo: Path) -> dict[str, Any]:
    path = repo / CONTRACT_RELATIVE
    _require(path.is_file(), f"missing contract: {CONTRACT_RELATIVE}")
    data = json.loads(path.read_text(encoding="utf-8"))
    _require(data["mechanism_id"] == "U5-P0-02", "mechanism id drift")
    _require(data["wave"] == "TE-U31", "wave drift")
    _require(data["decision_rule"]["retain_when"] == "expected_reacquisition_cost_units > expected_carry_cost_units", "retention rule drift")
    _require(data["provider_claim_policy"]["governor_alone_makes_provider_savings_claim"] is False, "provider claim boundary weakened")
    _require(data["rollback"]["enabled_false"] == "safe_retain_for_economic_pruning", "rollback boundary drift")
    return data


def _runtime_probe() -> dict[str, Any]:
    adaptive = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=512))
    original = adaptive.evaluate(
        "u5-p0-02 exact-head validation",
        [
            ContextPolicySignal(identity="expensive", token_count=160, relevance=0.1, recoverable=True),
            ContextPolicySignal(identity="cheap", token_count=120, relevance=0.1, recoverable=True),
        ],
    )
    governor = ReacquisitionTaxGovernor()
    governed = governor.govern_policy_result(
        original,
        [
            ContextCostEstimate(identity="expensive", expected_carry_tokens=30, expected_retrieval_tokens=70, expected_tool_tokens=20, tokenizer_method="VALIDATION_SYNTHETIC"),
            ContextCostEstimate(identity="cheap", expected_carry_tokens=100, expected_retrieval_tokens=10, expected_tool_tokens=5, tokenizer_method="VALIDATION_SYNTHETIC"),
        ],
    )
    by_identity = {row["identity"]: row for row in governed["decisions"]}
    _require(by_identity["expensive"]["recommended_action"] == "KEEP", "expensive reacquisition was pruned")
    _require(by_identity["cheap"]["recommended_action"] != "KEEP", "cheap reacquisition was incorrectly retained")
    _require(ReacquisitionTaxGovernor.verify_receipt(governed["reacquisition_tax_receipt"]), "tax receipt invalid")
    trace = ContextDecisionTrace.from_policy_result(governed)
    _require(ContextDecisionTrace.verify(trace), "governed policy no longer traces")
    governor.record_reacquisition(identity="cheap", event_kind="tool", retrieval_tokens=10, tool_tokens=5, latency_ms=2.0, source_ref="validation:tool")
    accounting = governor.accounting_receipt()
    _require(ReacquisitionTaxGovernor.verify_receipt(accounting), "accounting receipt invalid")
    _require(accounting["counterfactual_retention_tokens"] == 100, "counterfactual baseline drift")
    _require(accounting["reacquisition_tokens"]["total"] == 15, "reacquisition accounting drift")
    _require(accounting["accounted_net_token_delta"] == -85, "net token accounting drift")
    _require(accounting["provider_savings_claim"] is False, "validator must not create provider savings claim")
    return {
        "expensive_reacquisition_retained": True,
        "cheap_reacquisition_pruned": True,
        "later_tool_attributed": True,
        "counterfactual_retention_baseline": True,
        "net_end_to_end_accounting": True,
        "context_decision_trace_compatible": True,
        "provider_savings_claim": False,
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, TEST_RELATIVE, RUNTIME_RELATIVE, DOC_RELATIVE):
        _require((repo / relative).is_file(), f"missing enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: u5-p0-02-reacquisition-tax-${{ github.event.pull_request.number || github.ref }}" in workflow, "workflow concurrency is not PR/ref scoped")
    _require("tests.runtime.test_reacquisition_tax_governor" in workflow, "workflow lost dedicated runtime regressions")
    _require("tools/validate_u5_p0_02_reacquisition_tax_governor.py" in workflow, "workflow lost exact-head certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"action pin drift: {pin}")
    return {"workflow": WORKFLOW_RELATIVE.as_posix(), "tests": TEST_RELATIVE.as_posix(), "runtime": RUNTIME_RELATIVE.as_posix(), "doc": DOC_RELATIVE.as_posix()}


def certify(repo: Path) -> dict[str, Any]:
    contract = _load_contract(repo)
    runtime = _runtime_probe()
    enforcement = _validate_enforcement(repo)
    exact_head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all")
    _require(not dirty, "exact-head certification requires a clean repository")
    return {
        "ok": True,
        "claim": "U5_P0_02_REACQUISITION_TAX_GOVERNOR",
        "mechanism_id": contract["mechanism_id"],
        "wave": contract["wave"],
        "exact_head": exact_head,
        "admission_ready": True,
        "runtime": runtime,
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
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
