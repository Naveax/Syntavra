from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class HardwareIdentity:
    os: str
    architecture: str
    cpu: str
    logical_cores: int
    memory_bytes: int
    accelerator: str = ""
    runtime: str = ""

    @property
    def digest(self) -> str:
        return sha256_bytes(canonical_json(asdict(self)))


@dataclass(frozen=True)
class UsageReceipt:
    task_id: str
    arm_id: str
    repetition: int
    cache_mode: str
    provider: str
    request_id_hash: str
    provider_response_hash: str
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    quota_cost: float
    hardware_hash: str
    receipt_hash: str = ""

    def payload(self) -> dict[str, Any]:
        value = asdict(self); value.pop("receipt_hash", None); return value

    def expected_hash(self) -> str:
        return sha256_bytes(canonical_json(self.payload()))

    def validate(self) -> list[str]:
        reasons: list[str] = []
        if not self.task_id or not self.arm_id or self.repetition <= 0 or not self.cache_mode: reasons.append("receipt-identity-incomplete")
        if not self.provider or len(self.request_id_hash) != 64 or len(self.provider_response_hash) != 64: reasons.append("provider-evidence-incomplete")
        if any(value < 0 for value in (self.fresh_input_tokens, self.cached_input_tokens, self.output_tokens, self.reasoning_tokens)): reasons.append("negative-token-count")
        if not math.isfinite(self.quota_cost) or self.quota_cost <= 0: reasons.append("invalid-quota-cost")
        if len(self.hardware_hash) != 64: reasons.append("hardware-hash-invalid")
        if self.receipt_hash != self.expected_hash(): reasons.append("receipt-hash-mismatch")
        return reasons

    @classmethod
    def seal(cls, **values: Any) -> "UsageReceipt":
        provisional = cls(**values, receipt_hash="")
        return cls(**values, receipt_hash=provisional.expected_hash())


def _bootstrap_ci(values: list[float], *, samples: int = 5000, seed: int = 1337) -> tuple[float, float] | None:
    if not values: return None
    if len(values) == 1: return values[0], values[0]
    rng = random.Random(seed); size = len(values); medians = []
    for _ in range(samples):
        sample = sorted(values[rng.randrange(size)] for _ in range(size)); medians.append(sample[size // 2])
    medians.sort(); alpha = 0.025
    return medians[int(alpha * samples)], medians[min(samples - 1, int((1 - alpha) * samples) - 1)]


class HardenedSignalBench:
    """Failure-inclusive, identity-bound and receipt-gated comparison."""

    identity_fields = ("repository_tree", "prompt_hash", "verifier_hash", "permissions_hash", "cache_mode", "model", "reasoning", "context_window", "hardware_hash")

    @staticmethod
    def _value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
        return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)

    @classmethod
    def compare(cls, rows: Iterable[Mapping[str, Any] | Any], *, baseline_arm: str, candidate_arm: str, receipts: Iterable[UsageReceipt] = (), minimum_pairs: int = 10, require_receipts: bool = True) -> dict[str, Any]:
        rows = list(rows)
        receipt_index = {(item.task_id, item.repetition, item.cache_mode, item.arm_id): item for item in receipts}
        keyed = {(str(cls._value(row, "task_id", "")), int(cls._value(row, "repetition", 0)), str(cls._value(row, "cache_mode", "")), str(cls._value(row, "arm_id", ""))): row for row in rows}
        pair_keys = sorted({(task, repetition, cache) for task, repetition, cache, arm in keyed if arm == baseline_arm})
        ratios: list[float] = []; invalid = []; identity_mismatches = []; receipt_errors = []
        totals = {baseline_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0}, candidate_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0}}
        matched_pairs = 0
        for task_id, repetition, cache_mode in pair_keys:
            base = keyed.get((task_id, repetition, cache_mode, baseline_arm)); candidate = keyed.get((task_id, repetition, cache_mode, candidate_arm))
            if base is None or candidate is None:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "missing-arm"}); continue
            matched_pairs += 1
            mismatched = [field for field in cls.identity_fields if cls._value(base, field) is None or cls._value(base, field) != cls._value(candidate, field)]
            if mismatched: identity_mismatches.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "fields": mismatched})
            effective_quota = {}
            for arm, row in ((baseline_arm, base), (candidate_arm, candidate)):
                bucket = totals[arm]; bucket["attempts"] += 1
                success = bool(cls._value(row, "success", False) and cls._value(row, "verifier_success", False)); bucket["successes"] += int(success)
                bucket["work"] += float(cls._value(row, "verified_work", 0.0) or 0.0)
                bucket["security"] += int(cls._value(row, "security_regressions", 0) or 0); bucket["skips"] += int(cls._value(row, "verifier_skips", 0) or 0)
                quota = cls._value(row, "quota_cost"); receipt = receipt_index.get((task_id, repetition, cache_mode, arm))
                if receipt is not None:
                    reasons = receipt.validate()
                    if cls._value(row, "hardware_hash") != receipt.hardware_hash: reasons.append("receipt-hardware-mismatch")
                    if reasons: receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": reasons})
                    else: quota = receipt.quota_cost
                elif require_receipts: receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": ["receipt-missing"]})
                if quota is None or not math.isfinite(float(quota)) or float(quota) <= 0:
                    invalid.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reason": "quota-unavailable"}); effective_quota[arm] = None
                else:
                    effective_quota[arm] = float(quota); bucket["quota"] += float(quota)
            base_success = bool(cls._value(base, "success", False) and cls._value(base, "verifier_success", False)); candidate_success = bool(cls._value(candidate, "success", False) and cls._value(candidate, "verifier_success", False))
            equal_work = float(cls._value(base, "verified_work", 0.0) or 0.0) == float(cls._value(candidate, "verified_work", 0.0) or 0.0)
            if base_success and candidate_success and equal_work and effective_quota.get(baseline_arm) and effective_quota.get(candidate_arm): ratios.append(effective_quota[baseline_arm] / effective_quota[candidate_arm])
        pass_rates = {arm: bucket["successes"] / bucket["attempts"] if bucket["attempts"] else 0.0 for arm, bucket in totals.items()}
        utility = {arm: bucket["work"] / bucket["quota"] if bucket["quota"] > 0 else 0.0 for arm, bucket in totals.items()}
        aggregate_ratio = utility[candidate_arm] / utility[baseline_arm] if utility[baseline_arm] > 0 else 0.0
        ratios.sort(); ci = _bootstrap_ci(ratios); median = ratios[len(ratios) // 2] if ratios else None
        claimable = bool(matched_pairs >= minimum_pairs and len(ratios) >= minimum_pairs and ci and ci[0] > 1 and aggregate_ratio > 1 and pass_rates[candidate_arm] >= pass_rates[baseline_arm] and not identity_mismatches and not receipt_errors and totals[candidate_arm]["security"] == 0 and totals[candidate_arm]["skips"] == 0)
        return {"schema_version": 1, "baseline": baseline_arm, "candidate": candidate_arm, "matched_pairs": matched_pairs, "successful_equal_work_pairs": len(ratios), "median_success_pair_ratio": median, "confidence_interval_95": ci, "pass_rates": pass_rates, "total_verified_work": {arm: totals[arm]["work"] for arm in totals}, "total_quota": {arm: totals[arm]["quota"] for arm in totals}, "verified_work_per_quota": utility, "failure_inclusive_efficiency_ratio": aggregate_ratio, "identity_mismatches": identity_mismatches, "receipt_errors": receipt_errors, "invalid": invalid, "claimable_superiority": claimable, "claim": "SUPERIORITY_PROVEN" if claimable else "NOT_PROVEN"}
