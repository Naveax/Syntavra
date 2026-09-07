#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "contracts/python/recovery-to-99-v1.json"
WORKLOADS = ROOT / "contracts/python/token-economy-frozen-workloads-v1.json"
AUTHORITY = ROOT / "docs/CURRENT_RECOVERY_TO_99.md"
RUNTIME = ROOT / "syntavra_runtime/agent_runtime.py"
AUTONOMOUS = ROOT / "syntavra_runtime/autonomous_agent.py"
RETRIEVAL = ROOT / "syntavra_runtime/agent_retrieval.py"
RETRY = ROOT / "syntavra_runtime/retry_economics.py"
CONTEXT = ROOT / "syntavra_runtime/agent_context_runtime.py"
OBSERVATION = ROOT / "syntavra_runtime/provider_call_observation.py"
SECURITY_TEST = ROOT / "tests/runtime/test_recovery_to_99_security.py"
RUNTIME_TEST = ROOT / "tests/runtime/test_token_economy_rc1_runtime.py"
RETRIEVAL_TEST = ROOT / "tests/runtime/test_agent_retrieval.py"
RETRY_TEST = ROOT / "tests/runtime/test_retry_economics.py"
BENCHMARK = ROOT / "benchmarks/token_economy_rc1_benchmark.py"
WORKFLOW = ROOT / ".github/workflows/recovery-to-99-rc1.yml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _head() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _benchmark() -> dict[str, Any]:
    from benchmarks.token_economy_rc1_benchmark import run

    value = run()
    _require(value["claim_boundary"] == "LOCAL_STRUCTURAL_REGRESSION_ONLY_NOT_PROVIDER_BILLED_PROOF", "local benchmark claim boundary drift")
    _require(all(bool(item) for item in value["gates"].values()), "local structural benchmark gate failed")
    return value


def certify() -> dict[str, Any]:
    recovery = json.loads(RECOVERY.read_text(encoding="utf-8"))
    workloads = json.loads(WORKLOADS.read_text(encoding="utf-8"))
    authority = AUTHORITY.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    autonomous = AUTONOMOUS.read_text(encoding="utf-8")
    retrieval = RETRIEVAL.read_text(encoding="utf-8")
    retry = RETRY.read_text(encoding="utf-8")
    context = CONTEXT.read_text(encoding="utf-8")
    observation = OBSERVATION.read_text(encoding="utf-8")

    _require(recovery["schema_version"] == 1, "recovery schema drift")
    _require(recovery["family"] == "syntavra-recovery-to-99", "recovery family drift")
    _require(recovery["roadmap_freeze"] is True, "roadmap must remain frozen during recovery")
    _require(recovery["no_new_frontier_until_recovery_gate"] is True, "frontier freeze disabled")
    _require(recovery["no_new_capability_namespace"] is True, "parallel capability namespace admitted")
    _require(float(recovery["scoring_policy"]["target_minimum"]) >= 9.9, "target minimum weakened")
    _require(recovery["scoring_policy"]["local_tokenizer_counts_are_not_provider_billed_proof"] is True, "provider proof boundary weakened")
    _require(recovery["scoring_policy"]["provider_call_avoidance_requires_pre_inference_receipt_or_exact_replay_proof"] is True, "provider-call avoidance proof weakened")
    _require(len(recovery["canonical_owners"]) == 15, "canonical owner count drift")
    _require(len(set(recovery["canonical_owners"])) == 15, "duplicate canonical owner")

    expected_areas = {
        "architecture", "token_economics", "security", "runtime", "provider_proof",
        "benchmark", "ci", "roadmap", "roadmap_code_ratio", "product_potential", "readiness",
    }
    _require(set(recovery["areas"]) == expected_areas, "recovery area inventory drift")
    for name, row in recovery["areas"].items():
        _require(float(row["target"]) >= 9.9, f"{name} target weakened")
        _require(bool(row["required_gates"]), f"{name} has no required gates")

    runtime_gates = set(recovery["areas"]["runtime"]["required_gates"])
    for required in (
        "retry_economics_uses_exact_failure_plus_workspace_state_before_provider_call",
        "same_failure_on_changed_exact_state_remains_repairable",
        "uncertain_workspace_equivalence_never_suppresses_provider_inference",
    ):
        _require(required in runtime_gates, f"retry economics runtime gate missing: {required}")

    rows = workloads["workloads"]
    ids = [row["id"] for row in rows]
    _require(len(rows) == 10, "frozen workload corpus must contain B0-B9")
    _require(len(ids) == len(set(ids)), "duplicate workload id")
    _require(ids[0].startswith("B0-") and ids[-1].startswith("B9-"), "B0-B9 ordering drift")
    _require(workloads["pairing"]["minimum_provider_repetitions_per_claim"] >= 3, "provider repetition floor weakened")
    _require(workloads["pairing"]["failed_runs_count_in_total_cost"] is True, "failed run cost accounting disabled")

    for marker in (
        "R99-1: Constant-context runtime",
        "R99-2: Retrieval and tool-call elimination",
        "R99-3: Verified inference elimination",
        "R99-4: Repair-loop economics",
        "R99-5: Security/property closure",
        "R99-7: Provider proof",
        "R99-8: Release proof",
        "Provider proof cannot exceed `5.0` without E5 evidence",
    ):
        _require(marker in authority, f"authority marker missing: {marker}")

    runtime_markers = (
        "ConstantContextState",
        "provider_context_mode\": \"constant-active-evidence",
        "ToolOutputExternalizer",
        "ProviderCallObservationLedger",
        "prepare_input_tokens",
        "max_inspect_total_bytes",
        "QueryPushdownEngine",
        "search_inspect",
    )
    for marker in runtime_markers:
        _require(marker in runtime, f"runtime recovery marker missing: {marker}")
    for marker in ("UNCHANGED", "SUPERSEDED", "raw_history_replayed"):
        _require(marker in context, f"constant-context marker missing: {marker}")
    for marker in ("_filters", "unsupported repository filters", "unsupported repository projection fields"):
        _require(marker in retrieval, f"retrieval fail-closed marker missing: {marker}")
    for marker in (
        "RetryEconomicsGovernor",
        "workspace_state_fingerprint",
        "STOP_EXACT_REPEAT",
        "ALLOW_UNCERTAIN",
        "provider_calls_avoided",
    ):
        _require(marker in retry, f"retry economics marker missing: {marker}")
    for marker in ("RetryEconomicsGovernor", "workspace_state_fingerprint", "retry_economics"):
        _require(marker in autonomous, f"autonomous-agent retry integration missing: {marker}")
    for marker in ("provider_proof_complete", "paired_token_comparison", "provider_observed"):
        _require(marker in observation, f"provider observation marker missing: {marker}")

    for path in (SECURITY_TEST, RUNTIME_TEST, RETRIEVAL_TEST, RETRY_TEST, BENCHMARK):
        _require(path.is_file(), f"required recovery artifact missing: {path.relative_to(ROOT)}")

    retry_test = RETRY_TEST.read_text(encoding="utf-8")
    for marker in (
        "test_exact_unchanged_failure_state_stops_before_third_provider_call",
        "test_same_failure_on_changed_state_remains_repairable",
        "test_governor_stops_only_exact_failure_state_repeat",
    ):
        _require(marker in retry_test, f"retry regression missing: {marker}")

    benchmark = _benchmark()
    exact_head = _head()
    expected_head = os.environ.get("SYN_EXPECTED_HEAD", "").strip()
    if expected_head:
        _require(exact_head == expected_head, f"certifier is not running on expected head: {exact_head} != {expected_head}")

    provider_evidence = {
        "paired_real_provider_receipts_present_in_certification": False,
        "reason": "Provider credentials/network receipts are external evidence and are intentionally not fabricated by the offline RC1 certifier.",
        "score_cap_without_evidence": float(recovery["scoring_policy"]["provider_proof_score_may_not_exceed_5_0_without_paired_provider_observed_receipts"] and 5.0),
    }

    result = {
        "schema_version": 1,
        "family": "syntavra-recovery-to-99-certification",
        "ok": True,
        "exact_head": exact_head,
        "claim": "LOCAL_RECOVERY_GATES_PROVEN_PROVIDER_PROOF_STILL_EXTERNAL",
        "target": 9.9,
        "target_achieved": False,
        "roadmap_frozen": True,
        "workload_count": len(rows),
        "local_structural_benchmark": benchmark,
        "provider_evidence": provider_evidence,
        "required_next_external_gate": "paired provider-observed B0-B9 replay receipts plus exact-head green CI/dogfood",
        "hashes": {
            "recovery_contract": _sha(RECOVERY),
            "workload_contract": _sha(WORKLOADS),
            "current_authority": _sha(AUTHORITY),
            "agent_runtime": _sha(RUNTIME),
            "autonomous_agent": _sha(AUTONOMOUS),
            "agent_retrieval": _sha(RETRIEVAL),
            "retry_economics": _sha(RETRY),
            "agent_context_runtime": _sha(CONTEXT),
            "provider_call_observation": _sha(OBSERVATION),
            "security_test": _sha(SECURITY_TEST),
            "runtime_test": _sha(RUNTIME_TEST),
            "retrieval_test": _sha(RETRIEVAL_TEST),
            "retry_test": _sha(RETRY_TEST),
            "benchmark": _sha(BENCHMARK),
            "workflow": _sha(WORKFLOW) if WORKFLOW.is_file() else "",
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the code-first Syntavra recovery-to-9.9 gate without fabricating provider proof.")
    parser.add_argument("--output")
    args = parser.parse_args()
    value = certify()
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
