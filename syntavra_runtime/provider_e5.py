from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Mapping

from .util import canonical_json


E5_LEVEL = "E5_PAIRED_PROVIDER_RECEIPTS"
_HASH_FIELDS = (
    "request_id_hash",
    "provider_response_hash",
    "provider_receipt_hash",
    "usage_receipt_hash",
    "prompt_hash",
    "verifier_hash",
    "permissions_hash",
    "task_hash",
    "hardware_hash",
)
_PAIR_IDENTITY_FIELDS = (
    "task_hash",
    "repository_tree",
    "repository_commit",
    "prompt_hash",
    "verifier_hash",
    "permissions_hash",
    "model",
    "reasoning",
    "context_window",
    "hardware_hash",
    "provider",
    "timeout_seconds",
)


def _hex64(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _variants(contract: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    rows = contract.get("workloads", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("id"):
            continue
        variants = tuple(str(value) for value in row.get("variants", ()))
        result[str(row["id"])] = variants
    return result


def _document_hash_ok(document: Mapping[str, Any]) -> bool:
    stored = str(document.get("result_sha256") or "")
    if not _hex64(stored):
        return False
    payload = dict(document)
    payload.pop("result_sha256", None)
    observed = hashlib.sha256(canonical_json(payload)).hexdigest()
    return observed == stored


def certify_provider_e5(
    replay: Mapping[str, Any],
    workload_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate real paired provider evidence without depending on who won.

    E5 answers one question only: are the paired provider observations complete,
    identity-bound, failure-inclusive, and receipt-backed? Superiority is a
    separate claim and is intentionally not required for E5 validity.
    """

    failures: list[str] = []
    variants = _variants(workload_contract)
    expected_tasks = set(variants)
    mode = str(replay.get("mode") or "")
    baseline = str(replay.get("baseline_arm") or "")
    candidate = str(replay.get("candidate_arm") or "")
    repetitions = int(replay.get("repetitions") or 0)
    rows = replay.get("results", [])
    comparison = replay.get("comparison", {})

    if replay.get("family") != "syntavra-token-economy-provider-replay":
        failures.append("wrong-replay-family")
    if mode != "execute":
        failures.append("replay-not-executed")
    if not baseline or not candidate or baseline == candidate:
        failures.append("invalid-arm-identity")
    if repetitions < 3:
        failures.append("repetition-floor-not-met")
    if not expected_tasks:
        failures.append("empty-workload-contract")
    if not isinstance(rows, list) or not rows:
        failures.append("provider-results-missing")
        rows = []
    if not isinstance(comparison, Mapping):
        failures.append("comparison-missing")
        comparison = {}
    if replay.get("pair_identity_ok") is not True or replay.get("pair_issues") not in ([], ()):  # type: ignore[comparison-overlap]
        failures.append("pair-issues-present")
    if replay.get("result_sha256") is not None and not _document_hash_ok(replay):
        failures.append("replay-result-hash-invalid")

    expected_keys = {
        (task_id, repetition, cache_mode, arm)
        for task_id, modes in variants.items()
        for cache_mode in modes
        for repetition in range(1, repetitions + 1)
        for arm in (baseline, candidate)
    } if baseline and candidate and repetitions >= 1 else set()
    expected_pairs = len(expected_keys) // 2

    keyed: dict[tuple[str, int, str, str], Mapping[str, Any]] = {}
    providers: set[str] = set()
    model_identities: set[tuple[str, str, int, str]] = set()
    observed_receipts = 0
    failures_included = 0
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            failures.append(f"result:{index}:not-object")
            continue
        task_id = str(raw.get("task_id") or "")
        arm_id = str(raw.get("arm_id") or "")
        cache_mode = str(raw.get("cache_mode") or "")
        try:
            repetition = int(raw.get("repetition") or 0)
        except (TypeError, ValueError):
            repetition = 0
        key = (task_id, repetition, cache_mode, arm_id)
        if key in keyed:
            failures.append(f"duplicate-result:{task_id}:{cache_mode}:r{repetition}:{arm_id}")
            continue
        keyed[key] = raw
        if key not in expected_keys:
            failures.append(f"unexpected-result:{task_id}:{cache_mode}:r{repetition}:{arm_id}")

        if raw.get("provider_observed") is not True:
            failures.append(f"receipt-not-provider-observed:{task_id}:{arm_id}:r{repetition}:{cache_mode}")
        for field in _HASH_FIELDS:
            if not _hex64(raw.get(field)):
                failures.append(f"invalid-hash:{task_id}:{arm_id}:{field}")
        provider = str(raw.get("provider") or "")
        model = str(raw.get("model") or "")
        reasoning = str(raw.get("reasoning") or "")
        hardware = str(raw.get("hardware_hash") or "")
        try:
            context_window = int(raw.get("context_window") or 0)
        except (TypeError, ValueError):
            context_window = 0
        if not provider:
            failures.append(f"provider-missing:{task_id}:{arm_id}")
        else:
            providers.add(provider)
        if not model or not reasoning or context_window <= 0 or not hardware:
            failures.append(f"model-identity-incomplete:{task_id}:{arm_id}")
        else:
            model_identities.add((model, reasoning, context_window, hardware))
        quota = raw.get("quota_cost")
        try:
            quota_value = float(quota)
        except (TypeError, ValueError, OverflowError):
            quota_value = 0.0
        if quota_value <= 0:
            failures.append(f"quota-unavailable:{task_id}:{arm_id}")
        for token_field in ("fresh_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
            try:
                if int(raw.get(token_field) or 0) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                failures.append(f"invalid-token-count:{task_id}:{arm_id}:{token_field}")
        if _hex64(raw.get("usage_receipt_hash")):
            observed_receipts += 1
        if not bool(raw.get("success") and raw.get("verifier_success")):
            failures_included += 1

    missing = sorted(expected_keys - set(keyed))
    if missing:
        failures.append(f"missing-results:{len(missing)}")
    if len(keyed) != len(expected_keys):
        failures.append(f"result-count-mismatch:{len(keyed)}!={len(expected_keys)}")
    if len(providers) != 1:
        failures.append(f"provider-global-drift:{sorted(providers)}")
    if len(model_identities) != 1:
        failures.append("model-or-hardware-global-drift")

    for task_id, modes in variants.items():
        for cache_mode in modes:
            for repetition in range(1, repetitions + 1):
                left = keyed.get((task_id, repetition, cache_mode, baseline))
                right = keyed.get((task_id, repetition, cache_mode, candidate))
                if left is None or right is None:
                    continue
                mismatched = [field for field in _PAIR_IDENTITY_FIELDS if left.get(field) != right.get(field)]
                if mismatched:
                    failures.append(
                        f"pair-identity-mismatch:{task_id}:{cache_mode}:r{repetition}:{','.join(mismatched)}"
                    )

    if comparison:
        if comparison.get("comparison_authority") != "HardenedSignalBench.compare":
            failures.append("wrong-comparison-authority")
        if int(comparison.get("matched_pairs") or 0) != expected_pairs:
            failures.append("comparison-pair-count-mismatch")
        if int(comparison.get("provider_observed_pairs") or 0) != expected_pairs:
            failures.append("comparison-provider-observation-incomplete")
        for field in ("identity_mismatches", "receipt_errors", "invalid"):
            if comparison.get(field) not in ([], ()):  # fail closed on any comparator integrity error
                failures.append(f"comparison-{field}-present")

    unique_failures = list(dict.fromkeys(failures))
    complete = not unique_failures and len(expected_tasks) == 10 and observed_receipts == len(expected_keys)
    score = 9.3 if complete else (5.0 if mode == "execute" and rows else 2.0)
    return {
        "schema_version": 1,
        "claim": "E5_PROVIDER_PROOF_VALID" if complete else "E5_PROVIDER_PROOF_NOT_VALID",
        "claim_boundary": (
            "E5 certifies provider-observed paired measurement integrity only; it does not itself claim Syntavra superiority."
        ),
        "evidence_level": E5_LEVEL if complete else "BELOW_E5",
        "provider_evidence_complete": complete,
        "score": score,
        "workload_count": len(expected_tasks),
        "expected_result_count": len(expected_keys),
        "observed_result_count": len(keyed),
        "expected_pair_count": expected_pairs,
        "observed_receipt_count": observed_receipts,
        "failure_inclusive_result_count": failures_included,
        "provider": next(iter(providers), "") if len(providers) == 1 else "",
        "repetitions": repetitions,
        "failure_count": len(unique_failures),
        "failures": unique_failures,
    }


def certify_external_superiority(e5: Mapping[str, Any], replay: Mapping[str, Any]) -> dict[str, Any]:
    comparison = replay.get("comparison", {})
    if not isinstance(comparison, Mapping):
        comparison = {}
    e5_ok = bool(e5.get("provider_evidence_complete"))
    ci = comparison.get("confidence_interval_95")
    ci_low = float(ci[0]) if isinstance(ci, (list, tuple)) and len(ci) == 2 and ci[0] is not None else 0.0
    median = float(comparison.get("median_success_pair_ratio") or 0.0)
    failure_inclusive = float(comparison.get("failure_inclusive_efficiency_ratio") or 0.0)
    claimable = bool(e5_ok and comparison.get("claimable_superiority") and ci_low > 1.0)
    five_x = bool(claimable and ci_low >= 5.0 and median >= 5.0 and failure_inclusive >= 5.0)
    if five_x:
        claim = "5X_PAIRED_PROVIDER_SUPERIORITY_PROVEN"
        score = 9.7
    elif claimable:
        claim = "PAIRED_PROVIDER_SUPERIORITY_PROVEN"
        score = 9.3
    elif e5_ok:
        claim = "E5_VALID_SUPERIORITY_NOT_PROVEN"
        score = 6.0
    else:
        claim = "EXTERNAL_SUPERIORITY_NOT_PROVEN"
        score = 1.0
    return {
        "schema_version": 1,
        "claim": claim,
        "claim_boundary": "The claim applies only to the frozen paired baseline/candidate scope represented by the E5 replay; it is not universal market superiority.",
        "superiority_proven": claimable,
        "five_x_proven": five_x,
        "score": score,
        "baseline_arm": replay.get("baseline_arm"),
        "candidate_arm": replay.get("candidate_arm"),
        "successful_equal_work_pairs": int(comparison.get("successful_equal_work_pairs") or 0),
        "provider_observed_pairs": int(comparison.get("provider_observed_pairs") or 0),
        "median_success_pair_ratio": median or None,
        "failure_inclusive_efficiency_ratio": failure_inclusive or None,
        "confidence_interval_95": ci,
        "candidate_pass_rate": (comparison.get("pass_rates") or {}).get(replay.get("candidate_arm")),
        "baseline_pass_rate": (comparison.get("pass_rates") or {}).get(replay.get("baseline_arm")),
    }
