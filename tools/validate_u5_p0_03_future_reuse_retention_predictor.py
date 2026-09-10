from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from syntavra_runtime.context_governor import ContextCostEstimate, ReacquisitionTaxGovernor
from syntavra_runtime.context_value_predictor import (
    ContextValuePredictor,
    RetentionCandidate,
    RetentionEvaluation,
)


CONTRACT_RELATIVE = Path("contracts/python/u5-p0-03-future-reuse-retention-predictor-v1.json")
PUBLIC_SURFACE_RELATIVE = Path("contracts/engine/dual-engine-public-surface-v2.json")
RUNTIME_RELATIVE = Path("syntavra_runtime/context_value_predictor.py")
TEST_RELATIVE = Path("tests/runtime/test_future_reuse_retention_predictor.py")
WORKFLOW_RELATIVE = Path(".github/workflows/u5-p0-03-future-reuse-retention-predictor.yml")
DOC_RELATIVE = Path("docs/U5_P0_03_FUTURE_REUSE_RETENTION_PREDICTOR.md")

A = "a" * 64


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    ).strip()


def _load_contract(repo: Path) -> dict[str, Any]:
    path = repo / CONTRACT_RELATIVE
    _require(path.is_file(), f"missing contract: {CONTRACT_RELATIVE}")
    data = json.loads(path.read_text(encoding="utf-8"))
    _require(data["mechanism_id"] == "U5-P0-03", "mechanism id drift")
    _require(data["wave"] == "TE-U31", "wave drift")
    _require(data["classification"] == "HARDEN_SHADOW_FIRST", "classification drift")
    _require(
        data["retention_formula"]
        == "future_use_probability * expected_reacquisition_cost_units - carry_cost_units",
        "retention formula drift",
    )
    _require(data["label_policy"]["incomplete_horizon"] == "unlabeled_and_excluded", "incomplete-horizon boundary weakened")
    _require(data["promotion_policy"]["shadow_mode_default"] is True, "shadow-first default weakened")
    _require(data["promotion_policy"]["promotion_enabled_default"] is False, "promotion default weakened")
    _require(data["claim_policy"]["provider_savings_claim"] is False, "provider claim boundary weakened")
    return data


def _runtime_probe() -> dict[str, Any]:
    governor = ReacquisitionTaxGovernor()
    used = governor.decide(
        identity="used",
        original_action="EXTERNALIZE",
        estimate=ContextCostEstimate(
            identity="used",
            expected_carry_tokens=100,
            expected_retrieval_tokens=20,
            expected_tool_tokens=10,
        ),
        policy_hash=A,
    )
    unused = governor.decide(
        identity="unused",
        original_action="EXTERNALIZE",
        estimate=ContextCostEstimate(
            identity="unused",
            expected_carry_tokens=100,
            expected_retrieval_tokens=10,
        ),
        policy_hash=A,
    )
    _require(used.outcome == "PRUNE" and unused.outcome == "PRUNE", "probe requires two prior economic prunes")
    governor.record_reacquisition(
        identity="used",
        event_kind="tool",
        retrieval_tokens=30,
        tool_tokens=30,
        source_ref="validation:frozen-read",
    )
    accounting = governor.accounting_receipt()

    predictor = ContextValuePredictor()
    observations = predictor.observations_from_accounting(
        accounting,
        horizon_complete=True,
        feature_by_identity={"used": "edit", "unused": "edit"},
    )
    by_identity = {row.identity: row for row in observations}
    _require(by_identity["used"].future_used is True, "future-use positive label missing")
    _require(by_identity["unused"].future_used is False, "complete-horizon negative label missing")
    _require(by_identity["used"].observed_reacquisition_cost_units == 60, "observed reacquisition cost drift")

    fit = predictor.fit(observations)
    _require(ContextValuePredictor.verify_receipt(fit), "fit receipt invalid")
    estimate = predictor.estimate("edit", fallback_reacquisition_cost_units=999)
    _require(estimate.future_use_probability == 0.5, "beta-smoothed future-use probability drift")
    _require(estimate.expected_reacquisition_cost_units == 60, "positive reacquisition-cost estimate drift")

    prediction = predictor.predict(
        RetentionCandidate(
            identity="candidate",
            baseline_action="EXTERNALIZE",
            feature_key="edit",
            carry_cost_units=10,
            fallback_reacquisition_cost_units=60,
        )
    )
    _require(prediction.recommended_action == "KEEP", "positive retention value did not recommend KEEP")
    _require(prediction.effective_action == "EXTERNALIZE", "shadow mode changed runtime action")
    _require(prediction.retention_value > 0, "positive retention-value probe drift")

    hard_pin = predictor.predict(
        RetentionCandidate(
            identity="verifier",
            baseline_action="EXTERNALIZE",
            feature_key="edit",
            carry_cost_units=999,
            fallback_reacquisition_cost_units=1,
            mandatory_verifier_evidence=True,
        )
    )
    _require(hard_pin.hard_pinned is True, "mandatory verifier evidence left learned decision plane")
    _require(hard_pin.effective_action == "KEEP", "mandatory verifier evidence not pinned")

    incomplete = ContextValuePredictor.observations_from_accounting(
        accounting,
        horizon_complete=False,
        feature_by_identity={"used": "edit", "unused": "edit"},
    )
    incomplete_predictor = ContextValuePredictor()
    incomplete_fit = incomplete_predictor.fit(incomplete)
    _require(incomplete_fit["complete_label_count"] == 0, "incomplete horizon generated labels")
    _require(incomplete_fit["excluded_incomplete_count"] == 2, "incomplete-horizon exclusion drift")

    gate = predictor.evaluate_promotion(
        RetentionEvaluation(
            baseline_task_success_rate=1.0,
            candidate_task_success_rate=1.0,
            mandatory_evidence_misses=0,
            baseline_reacquisition_cost_units=60,
            candidate_reacquisition_cost_units=50,
            baseline_provider_cost_per_success=100,
            candidate_provider_cost_per_success=90,
        )
    )
    _require(gate["evidence_gate_ok"] is True, "clean promotion evidence gate unexpectedly failed")
    _require(gate["promotion_allowed"] is False, "default shadow policy promoted itself")
    _require(gate["provider_savings_claim"] is False, "predictor created provider savings claim")

    status = predictor.status()
    _require(status["provider_calls"] == 0 and status["provider_tokens"] == 0, "prediction path hid provider work")
    _require(status["raw_context_authority"] is False, "predictor became raw context authority")
    _require(status["semantic_state_authority"] is False, "predictor became semantic state authority")

    return {
        "complete_horizon_labels": True,
        "incomplete_horizon_excluded": True,
        "beta_smoothed_probability": estimate.future_use_probability,
        "expected_reacquisition_cost_units": estimate.expected_reacquisition_cost_units,
        "positive_retention_value_recommends_keep": True,
        "shadow_does_not_change_effective_action": True,
        "mandatory_verifier_evidence_hard_pinned": True,
        "clean_gate_does_not_auto_promote": True,
        "provider_calls": status["provider_calls"],
        "provider_tokens": status["provider_tokens"],
        "provider_savings_claim": False,
    }


def _validate_public_surface(repo: Path) -> dict[str, Any]:
    path = repo / PUBLIC_SURFACE_RELATIVE
    _require(path.is_file(), f"missing public-surface contract: {PUBLIC_SURFACE_RELATIVE}")
    data = json.loads(path.read_text(encoding="utf-8"))
    python_surface = data.get("python_surface")
    _require(isinstance(python_surface, dict), "python public surface missing")
    _require(python_surface.get("module_count") == 234, "python module-count snapshot must be 234 at U5-P0-03 exact head")
    _require(python_surface.get("public_command_count") == 245, "U5-P0-03 must not change public command count")
    return {
        "module_count": python_surface["module_count"],
        "public_command_count": python_surface["public_command_count"],
        "command_paths_sha256": python_surface["command_paths_sha256"],
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (RUNTIME_RELATIVE, TEST_RELATIVE, WORKFLOW_RELATIVE, DOC_RELATIVE):
        _require((repo / relative).is_file(), f"missing U5-P0-03 enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require(
        "group: u5-p0-03-future-reuse-retention-${{ github.event.pull_request.number || github.ref }}" in workflow,
        "workflow concurrency is not PR/ref scoped",
    )
    _require("tests.runtime.test_future_reuse_retention_predictor" in workflow, "workflow lost dedicated regressions")
    _require("tools/validate_u5_p0_03_future_reuse_retention_predictor.py" in workflow, "workflow lost exact-head certifier")
    _require("tools/verify_dual_engine_public_surface.py" in workflow, "workflow lost public-surface drift guard")
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
        "claim": "U5_P0_03_FUTURE_REUSE_RETENTION_PREDICTOR",
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
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
