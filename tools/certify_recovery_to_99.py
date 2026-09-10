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
PROVIDER_E5 = ROOT / "syntavra_runtime/provider_e5.py"
SANDBOX = ROOT / "syntavra_runtime/execution_sandbox.py"
SECURITY_TEST = ROOT / "tests/runtime/test_recovery_to_99_security.py"
PROPERTY_TEST = ROOT / "tests/runtime/test_recovery_to_99_property_matrix.py"
SANDBOX_TEST = ROOT / "tests/runtime/test_sandbox_capability_probe.py"
PROVIDER_E5_TEST = ROOT / "tests/runtime/test_provider_e5.py"
RUNTIME_TEST = ROOT / "tests/runtime/test_token_economy_rc1_runtime.py"
RETRIEVAL_TEST = ROOT / "tests/runtime/test_agent_retrieval.py"
RETRY_TEST = ROOT / "tests/runtime/test_retry_economics.py"
FROZEN_CORPUS_TEST = ROOT / "tests/runtime/test_token_economy_frozen_corpus.py"
PROVIDER_REPLAY_TEST = ROOT / "tests/runtime/test_token_economy_provider_replay.py"
BENCHMARK = ROOT / "benchmarks/token_economy_rc1_benchmark.py"
FROZEN_CORPUS = ROOT / "benchmarks/token_economy_frozen_corpus.py"
PROVIDER_REPLAY = ROOT / "benchmarks/token_economy_provider_replay.py"
MULTI_SCOPE_CERTIFIER = ROOT / "tools/certify_provider_multi_scope.py"
WORKFLOW = ROOT / ".github/workflows/recovery-to-99-rc1.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release-main-merge-gate.yml"


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
    _require(
        value["claim_boundary"] == "LOCAL_STRUCTURAL_REGRESSION_ONLY_NOT_PROVIDER_BILLED_PROOF",
        "local benchmark claim boundary drift",
    )
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
    provider_e5 = PROVIDER_E5.read_text(encoding="utf-8")
    sandbox = SANDBOX.read_text(encoding="utf-8")
    frozen_corpus_source = FROZEN_CORPUS.read_text(encoding="utf-8")
    frozen_corpus_test = FROZEN_CORPUS_TEST.read_text(encoding="utf-8")
    provider_replay_source = PROVIDER_REPLAY.read_text(encoding="utf-8")
    provider_replay_test = PROVIDER_REPLAY_TEST.read_text(encoding="utf-8")
    property_test = PROPERTY_TEST.read_text(encoding="utf-8")
    sandbox_test = SANDBOX_TEST.read_text(encoding="utf-8")
    provider_e5_test = PROVIDER_E5_TEST.read_text(encoding="utf-8")
    multi_scope_certifier = MULTI_SCOPE_CERTIFIER.read_text(encoding="utf-8")
    recovery_workflow = WORKFLOW.read_text(encoding="utf-8")
    release_workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    _require(recovery["schema_version"] == 1, "recovery schema drift")
    _require(recovery["family"] == "syntavra-recovery-to-99", "recovery family drift")
    _require(recovery["roadmap_freeze"] is True, "roadmap must remain frozen during recovery")
    _require(recovery["no_new_frontier_until_recovery_gate"] is True, "frontier freeze disabled")
    _require(recovery["no_new_capability_namespace"] is True, "parallel capability namespace admitted")
    target = float(recovery["scoring_policy"]["target_minimum"])
    _require(target >= 10.0, "recovery target minimum is below 10.0")
    _require(recovery["scoring_policy"]["local_tokenizer_counts_are_not_provider_billed_proof"] is True, "provider proof boundary weakened")
    _require(recovery["scoring_policy"]["provider_call_avoidance_requires_pre_inference_receipt_or_exact_replay_proof"] is True, "provider-call avoidance proof weakened")
    _require(recovery["scoring_policy"]["provider_replay_plan_is_not_provider_proof"] is True, "provider replay plan was allowed to masquerade as E5 proof")
    _require(recovery["scoring_policy"]["external_superiority_score_may_not_reach_10_0_without_two_independent_provider_model_scopes"] is True, "multi-scope superiority cap weakened")
    _require(len(recovery["canonical_owners"]) == 15, "canonical owner count drift")
    _require(len(set(recovery["canonical_owners"])) == 15, "duplicate canonical owner")

    expected_areas = {
        "architecture", "token_economics", "security", "runtime", "provider_proof",
        "benchmark", "ci", "roadmap", "roadmap_code_ratio", "product_potential", "readiness",
    }
    _require(set(recovery["areas"]) == expected_areas, "recovery area inventory drift")
    for name, row in recovery["areas"].items():
        _require(float(row["target"]) >= 10.0, f"{name} target is below 10.0")
        _require(bool(row["required_gates"]), f"{name} has no required gates")

    runtime_gates = set(recovery["areas"]["runtime"]["required_gates"])
    for required in (
        "retry_economics_uses_exact_failure_plus_workspace_state_before_provider_call",
        "same_failure_on_changed_exact_state_remains_repairable",
        "uncertain_workspace_equivalence_never_suppresses_provider_inference",
        "sandbox_backend_admission_requires_runtime_capability_probe",
    ):
        _require(required in runtime_gates, f"runtime gate missing: {required}")

    security_gates = set(recovery["areas"]["security"]["required_gates"])
    for required in (
        "seeded_property_matrix_required_by_recovery_and_release_gates",
        "native_sandbox_capability_probed_not_inferred_from_binary_presence",
        "strict_native_probe_failure_is_fail_closed",
    ):
        _require(required in security_gates, f"security gate missing: {required}")

    provider_gates = set(recovery["areas"]["provider_proof"]["required_gates"])
    for required in (
        "paired_provider_observed_baseline_and_candidate_receipts",
        "same_model_provider_task_repo_verifier_and_effort_within_pair",
        "minimum_three_repetitions_per_claimed_workload",
        "frozen_replay_pair_identity_has_zero_issues",
        "arm_versions_stable_and_bound_into_e5_scope",
        "local_estimates_and_replay_plans_never_upgrade_claim_level",
    ):
        _require(required in provider_gates, f"provider proof gate missing: {required}")

    benchmark_gates = set(recovery["areas"]["benchmark"]["required_gates"])
    for required in (
        "executable_portable_b0_b9_corpus",
        "workload_variant_schedule_matches_contract_exactly",
        "provider_replay_plan_uses_same_frozen_task_identity_for_both_arms",
        "workload_level_non_regression_required_for_superiority",
        "multi_scope_superiority_requires_two_independent_provider_model_scopes",
    ):
        _require(required in benchmark_gates, f"benchmark gate missing: {required}")

    ci_gates = set(recovery["areas"]["ci"]["required_gates"])
    for required in (
        "executable_frozen_corpus_and_provider_replay_plan_regression_required",
        "security_sandbox_and_exact_recovery_property_regressions_required",
        "provider_e5_claim_semantics_required",
        "release_gate_reproves_recovery_critical_security_and_runtime_tests",
    ):
        _require(required in ci_gates, f"CI gate missing: {required}")

    rows = workloads["workloads"]
    ids = [row["id"] for row in rows]
    _require(len(rows) == 10, "frozen workload corpus must contain B0-B9")
    _require(len(ids) == len(set(ids)), "duplicate workload id")
    _require(ids[0].startswith("B0-") and ids[-1].startswith("B9-"), "B0-B9 ordering drift")
    _require(workloads["pairing"]["minimum_provider_repetitions_per_claim"] >= 3, "provider repetition floor weakened")
    _require(workloads["pairing"]["failed_runs_count_in_total_cost"] is True, "failed run cost accounting disabled")
    _require(workloads["pairing"]["same_frozen_workload_identity"] is True, "portable workload pairing disabled")
    _require(workloads["pairing"]["pre_inference_provider_calls_avoided_reported_separately"] is True, "provider-call avoidance accounting merged into compression")

    executable = workloads.get("executable_corpus")
    _require(isinstance(executable, dict), "executable frozen corpus contract missing")
    for key in (
        "local_repository_locator_excluded_from_portable_identity",
        "fixed_commit_metadata",
        "repository_tree_and_commit_must_match_across_materialization_roots",
        "all_initial_fixtures_must_fail_verifier",
        "dirty_fixture_forbidden",
        "submodule_fixture_forbidden",
        "provider_credentials_required_for_provider_proof",
    ):
        _require(executable.get(key) is True, f"executable corpus policy weakened: {key}")
    _require(executable.get("materializer") == "benchmarks/token_economy_frozen_corpus.py", "frozen corpus materializer drift")
    _require(executable.get("regression_test") == "tests/runtime/test_token_economy_frozen_corpus.py", "frozen corpus regression test drift")
    _require(executable.get("verifier_transport") == "inline-immutable-task-contract", "frozen verifier transport weakened")

    for marker in (
        "R99-1: Constant-context runtime",
        "R99-2: Retrieval and tool-call elimination",
        "R99-3: Verified inference elimination",
        "R99-4: Repair-loop economics",
        "R99-5: Security/property closure",
        "R99-7: Provider proof",
        "R99-8: Release proof",
        "Provider proof cannot exceed `5.0` without E5 evidence",
        "Area-specific 10.0 definition",
    ):
        _require(marker in authority, f"authority marker missing: {marker}")

    for marker in (
        "ConstantContextState",
        "provider_context_mode\": \"constant-active-evidence",
        "ToolOutputExternalizer",
        "ProviderCallObservationLedger",
        "prepare_input_tokens",
        "max_inspect_total_bytes",
        "QueryPushdownEngine",
        "search_inspect",
    ):
        _require(marker in runtime, f"runtime recovery marker missing: {marker}")
    for marker in ("UNCHANGED", "SUPERSEDED", "raw_history_replayed"):
        _require(marker in context, f"constant-context marker missing: {marker}")
    for marker in ("_filters", "unsupported repository filters", "unsupported repository projection fields"):
        _require(marker in retrieval, f"retrieval fail-closed marker missing: {marker}")
    for marker in ("RetryEconomicsGovernor", "workspace_state_fingerprint", "STOP_EXACT_REPEAT", "ALLOW_UNCERTAIN", "provider_calls_avoided"):
        _require(marker in retry, f"retry economics marker missing: {marker}")
    for marker in ("RetryEconomicsGovernor", "workspace_state_fingerprint", "retry_economics"):
        _require(marker in autonomous, f"autonomous-agent retry integration missing: {marker}")
    for marker in ("provider_proof_complete", "paired_token_comparison", "provider_observed"):
        _require(marker in observation, f"provider observation marker missing: {marker}")

    for marker in (
        "certify_provider_e5",
        "certify_external_superiority",
        "certify_multi_scope_superiority",
        "workload_non_regression",
        "arm-version-global-drift",
        "MULTI_SCOPE_EXTERNAL_SUPERIORITY_PROVEN",
    ):
        _require(marker in provider_e5, f"provider E5 hardening marker missing: {marker}")
    for marker in ("_probe_native", "native capability probe", "portable-process-boundary", "strict_native"):
        _require(marker in sandbox, f"sandbox capability marker missing: {marker}")

    for marker in (
        "frozen_workload_identity",
        "portable_identity_sha256",
        "inline-immutable-task-contract",
        "--object-format=sha1",
        "verify_initial_failures",
    ):
        _require(marker in frozen_corpus_source, f"executable corpus marker missing: {marker}")
    for marker in (
        "test_two_materialization_roots_have_identical_portable_identity",
        "test_every_initial_fixture_fails_its_immutable_verifier",
        "test_portable_identity_excludes_local_repository_locator",
    ):
        _require(marker in frozen_corpus_test, f"executable corpus regression missing: {marker}")

    for marker in (
        "build_schedule",
        "minimum three repetitions",
        "provider-receipt-missing",
        "pair-identity-mismatch",
        "provider-mismatch",
        "REPLAY_PLAN_ONLY_NOT_PROVIDER_PROOF",
        "provider_proof_complete",
    ):
        _require(marker in provider_replay_source, f"provider replay marker missing: {marker}")
    for marker in (
        "test_schedule_respects_frozen_workload_variants_exactly",
        "test_repetition_floor_is_fail_closed",
        "test_explicit_credential_values_are_forbidden",
        "test_plan_is_offline_and_receipt_claim_stays_closed",
    ):
        _require(marker in provider_replay_test, f"provider replay regression missing: {marker}")

    for marker in (
        "test_inference_skip_identity_mutation_matrix_never_aliases",
        "test_retry_equivalence_property_matrix",
        "test_corrupt_ciphertext_never_returns_partial_plaintext",
    ):
        _require(marker in property_test, f"seeded recovery property regression missing: {marker}")
    for marker in (
        "test_binary_presence_without_capability_falls_back_portably",
        "test_strict_native_rejects_probe_failure",
        "test_successful_probe_admits_native_backend",
    ):
        _require(marker in sandbox_test, f"sandbox capability regression missing: {marker}")
    for marker in (
        "test_workload_regression_blocks_superiority_even_when_aggregate_flag_is_true",
        "test_multi_scope_superiority_requires_two_independent_provider_model_scopes",
        "test_arm_version_drift_invalidates_e5",
    ):
        _require(marker in provider_e5_test, f"provider E5 regression missing: {marker}")
    for marker in ("certify_multi_scope_superiority", "raw E5 replays", "--require-multi-scope"):
        _require(marker in multi_scope_certifier, f"multi-scope certifier marker missing: {marker}")

    for path in (
        SECURITY_TEST,
        PROPERTY_TEST,
        SANDBOX_TEST,
        PROVIDER_E5_TEST,
        RUNTIME_TEST,
        RETRIEVAL_TEST,
        RETRY_TEST,
        FROZEN_CORPUS_TEST,
        PROVIDER_REPLAY_TEST,
        BENCHMARK,
        FROZEN_CORPUS,
        PROVIDER_REPLAY,
        PROVIDER_E5,
        SANDBOX,
        MULTI_SCOPE_CERTIFIER,
        WORKFLOW,
        RELEASE_WORKFLOW,
    ):
        _require(path.is_file(), f"required recovery artifact missing: {path.relative_to(ROOT)}")

    retry_test = RETRY_TEST.read_text(encoding="utf-8")
    for marker in (
        "test_exact_unchanged_failure_state_stops_before_third_provider_call",
        "test_same_failure_on_changed_state_remains_repairable",
        "test_governor_stops_only_exact_failure_state_repeat",
    ):
        _require(marker in retry_test, f"retry regression missing: {marker}")

    for marker in (
        "tests.runtime.test_recovery_to_99_property_matrix",
        "tests.runtime.test_sandbox_capability_probe",
        "tests.runtime.test_provider_e5",
    ):
        _require(marker in recovery_workflow, f"recovery workflow lost required test: {marker}")
    for marker in (
        "tests.runtime.test_recovery_to_99_property_matrix",
        "tests.runtime.test_sandbox_capability_probe",
        "tools/certify_recovery_to_99.py",
        "tools/certify_release_integrity.py",
    ):
        _require(marker in release_workflow, f"release workflow lost recovery/release proof: {marker}")

    benchmark = _benchmark()
    exact_head = _head()
    expected_head = os.environ.get("SYN_EXPECTED_HEAD", "").strip()
    if expected_head:
        _require(exact_head == expected_head, f"certifier is not running on expected head: {exact_head} != {expected_head}")

    provider_evidence = {
        "paired_real_provider_receipts_present_in_certification": False,
        "replay_planner_present": True,
        "e5_and_multi_scope_proof_machinery_present": True,
        "reason": "Provider credentials/network receipts are external evidence and are intentionally not fabricated by the offline recovery certifier; deterministic replay/E5/multi-scope machinery is infrastructure, not a live E5 result.",
        "score_cap_without_evidence": float(recovery["scoring_policy"]["provider_proof_score_may_not_exceed_5_0_without_paired_provider_observed_receipts"] and 5.0),
    }

    result = {
        "schema_version": 2,
        "family": "syntavra-recovery-to-99-certification",
        "ok": True,
        "exact_head": exact_head,
        "claim": "LOCAL_RECOVERY_GATES_PROVEN_PROVIDER_PROOF_STILL_EXTERNAL",
        "target": target,
        "target_achieved": False,
        "roadmap_frozen": True,
        "workload_count": len(rows),
        "executable_frozen_corpus": True,
        "provider_replay_planner": True,
        "e5_proof_machinery": True,
        "multi_scope_superiority_machinery": True,
        "sandbox_capability_probe": True,
        "seeded_property_matrix_required": True,
        "release_reproves_recovery": True,
        "local_structural_benchmark": benchmark,
        "provider_evidence": provider_evidence,
        "required_next_external_gate": "paired provider-observed B0-B9 E5 receipts, two independent provider/model superiority scopes, exact-head green CI, dogfood and zero-known-P0/P1 release proof",
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
            "provider_e5": _sha(PROVIDER_E5),
            "execution_sandbox": _sha(SANDBOX),
            "security_test": _sha(SECURITY_TEST),
            "property_test": _sha(PROPERTY_TEST),
            "sandbox_test": _sha(SANDBOX_TEST),
            "provider_e5_test": _sha(PROVIDER_E5_TEST),
            "runtime_test": _sha(RUNTIME_TEST),
            "retrieval_test": _sha(RETRIEVAL_TEST),
            "retry_test": _sha(RETRY_TEST),
            "frozen_corpus_test": _sha(FROZEN_CORPUS_TEST),
            "provider_replay_test": _sha(PROVIDER_REPLAY_TEST),
            "benchmark": _sha(BENCHMARK),
            "frozen_corpus": _sha(FROZEN_CORPUS),
            "provider_replay": _sha(PROVIDER_REPLAY),
            "multi_scope_certifier": _sha(MULTI_SCOPE_CERTIFIER),
            "workflow": _sha(WORKFLOW),
            "release_workflow": _sha(RELEASE_WORKFLOW),
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the code-first Syntavra recovery-to-10 gate without fabricating provider proof.")
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
