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
        if not self.task_id or not self.arm_id or self.repetition <= 0 or not self.cache_mode:
            reasons.append("receipt-identity-incomplete")

        def valid_sha256(value: str) -> bool:
            return len(value) == 64 and value == value.casefold() and all(ch in "0123456789abcdef" for ch in value)

        if not self.provider or not valid_sha256(self.request_id_hash) or not valid_sha256(self.provider_response_hash):
            reasons.append("provider-evidence-incomplete")
        if any(value < 0 for value in (self.fresh_input_tokens, self.cached_input_tokens, self.output_tokens, self.reasoning_tokens)):
            reasons.append("negative-token-count")
        if not math.isfinite(self.quota_cost) or self.quota_cost <= 0:
            reasons.append("invalid-quota-cost")
        if not valid_sha256(self.hardware_hash):
            reasons.append("hardware-hash-invalid")
        if not valid_sha256(self.receipt_hash):
            reasons.append("receipt-hash-invalid")
        elif self.receipt_hash != self.expected_hash():
            reasons.append("receipt-hash-mismatch")
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
        receipt_rows = list(receipts)
        invalid: list[dict[str, Any]] = []
        identity_mismatches: list[dict[str, Any]] = []
        receipt_errors: list[dict[str, Any]] = []
        def legacy_provider_row(row) -> bool:
            request_hash = str(cls._value(row, "request_id_hash", "") or "")
            provider_receipt_hash = str(cls._value(row, "provider_receipt_hash", "") or "")

            def valid_sha256(value: str) -> bool:
                return (
                    len(value) == 64
                    and value == value.casefold()
                    and all(ch in "0123456789abcdef" for ch in value)
                )

            return bool(
                cls._value(row, "provider_observed", False)
                and str(cls._value(row, "provider", "") or "")
                and str(cls._value(row, "model", "") or "")
                and valid_sha256(request_hash)
                and valid_sha256(provider_receipt_hash)
                and not str(cls._value(row, "usage_receipt_hash", "") or "")
                and not str(cls._value(row, "provider_response_hash", "") or "")
                and not str(cls._value(row, "arm_version", "") or "")
            )


        keyed: dict[tuple[str, int, str, str], Mapping[str, Any] | Any] = {}
        for row in rows:
            key = (
                str(cls._value(row, "task_id", "")),
                int(cls._value(row, "repetition", 0)),
                str(cls._value(row, "cache_mode", "")),
                str(cls._value(row, "arm_id", "")),
            )
            if key in keyed:
                invalid.append({"task": key[0], "arm": key[3], "repetition": key[1], "cache": key[2], "reason": "duplicate-result-key"})
                continue
            keyed[key] = row

        receipt_index: dict[tuple[str, int, str, str], UsageReceipt] = {}
        for item in receipt_rows:
            key = (item.task_id, item.repetition, item.cache_mode, item.arm_id)
            if key in receipt_index:
                receipt_errors.append({"task": item.task_id, "arm": item.arm_id, "repetition": item.repetition, "cache": item.cache_mode, "reasons": ["receipt-duplicate"]})
                continue
            receipt_index[key] = item

        strict_rows = [row for row in rows if bool(cls._value(row, "usage_receipt_hash", ""))]
        for task_id in sorted({str(cls._value(row, "task_id", "")) for row in strict_rows}):
            task_rows = [row for row in strict_rows if str(cls._value(row, "task_id", "")) == task_id]
            for field in ("repository_commit", "repository_tree", "task_hash", "prompt_hash", "verifier_hash", "permissions_hash", "timeout_seconds"):
                values = []
                missing = False
                for row in task_rows:
                    value = cls._value(row, field, None)
                    if value is None or value == "":
                        missing = True
                        continue
                    if field == "timeout_seconds":
                        try:
                            value = float(value)
                            if not math.isfinite(value) or value <= 0:
                                missing = True
                                continue
                        except (TypeError, ValueError, OverflowError):
                            missing = True
                            continue
                    values.append(value)
                if missing or len(set(values)) != 1:
                    identity_mismatches.append({"task": task_id, "scope": "task-global", "fields": [field]})

        for arm in (baseline_arm, candidate_arm):
            arm_rows = [row for row in strict_rows if str(cls._value(row, "arm_id", "")) == arm]
            versions = {str(cls._value(row, "arm_version", "")) for row in arm_rows if str(cls._value(row, "arm_version", ""))}
            if not arm_rows or len(versions) != 1 or any(not str(cls._value(row, "arm_version", "")) for row in arm_rows):
                identity_mismatches.append({"arm": arm, "scope": "arm-global", "fields": ["arm_version"]})

        for field in ("provider", "model", "reasoning", "context_window", "hardware_hash"):
            values = []
            missing = False
            for row in strict_rows:
                value = cls._value(row, field, None)
                if value is None or value == "":
                    missing = True
                    continue
                if field == "context_window":
                    try:
                        value = int(value)
                        if value <= 0:
                            missing = True
                            continue
                    except (TypeError, ValueError):
                        missing = True
                        continue
                values.append(value)
            if strict_rows and (missing or len(set(values)) != 1):
                identity_mismatches.append({"scope": "comparison-global", "fields": [field]})

        seen_requests: dict[tuple[str, str], tuple[str, int, str]] = {}
        for item in receipt_rows:
            key = (item.arm_id, item.request_id_hash)
            identity = (item.task_id, item.repetition, item.cache_mode)
            previous = seen_requests.get(key)
            if previous is not None and previous != identity:
                receipt_errors.append({
                    "task": item.task_id, "arm": item.arm_id, "repetition": item.repetition,
                    "cache": item.cache_mode, "reasons": ["provider-request-reused"],
                })
            else:
                seen_requests[key] = identity

        pair_keys = sorted({
            (task, repetition, cache)
            for task, repetition, cache, arm in keyed
            if arm in {baseline_arm, candidate_arm}
        })
        ratios: list[float] = []
        totals = {
            baseline_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0},
            candidate_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0},
        }
        matched_pairs = 0

        for task_id, repetition, cache_mode in pair_keys:
            base = keyed.get((task_id, repetition, cache_mode, baseline_arm))
            candidate = keyed.get((task_id, repetition, cache_mode, candidate_arm))
            for arm, row in ((baseline_arm, base), (candidate_arm, candidate)):
                bucket = totals[arm]
                bucket["attempts"] += 1
                if row is None:
                    continue
                success = bool(cls._value(row, "success", False) and cls._value(row, "verifier_success", False))
                bucket["successes"] += int(success)
                bucket["work"] += float(cls._value(row, "verified_work", 0.0) or 0.0)
                bucket["security"] += int(cls._value(row, "security_regressions", 0) or 0)
                bucket["skips"] += int(cls._value(row, "verifier_skips", 0) or 0)

            if base is None or candidate is None:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "missing-arm", "missing": baseline_arm if base is None else candidate_arm})
                continue
            matched_pairs += 1

            mismatched: list[str] = []
            strict_pair_identity = bool(
                cls._value(base, "usage_receipt_hash", "")
                or cls._value(candidate, "usage_receipt_hash", "")
            )
            legacy_provider_pair = legacy_provider_row(base) and legacy_provider_row(candidate)
            legacy_identity_fields = {
                "repository_tree",
                "prompt_hash",
                "verifier_hash",
                "permissions_hash",
                "cache_mode",
                "model",
                "reasoning",
                "context_window",
                "hardware_hash",
            }
            if legacy_provider_pair:
                legacy_identity_fields -= {"reasoning", "context_window", "hardware_hash"}
            for field in cls.identity_fields:
                if not strict_pair_identity and field not in legacy_identity_fields:
                    continue
                base_value = cls._value(base, field)
                candidate_value = cls._value(candidate, field)
                missing = base_value is None or candidate_value is None or base_value == "" or candidate_value == ""
                if field == "context_window":
                    try:
                        missing = missing or int(base_value) <= 0 or int(candidate_value) <= 0
                    except (TypeError, ValueError):
                        missing = True
                if missing or base_value != candidate_value:
                    mismatched.append(field)
            base_receipt = receipt_index.get((task_id, repetition, cache_mode, baseline_arm))
            candidate_receipt = receipt_index.get((task_id, repetition, cache_mode, candidate_arm))
            base_provider = str(cls._value(base, "provider", "") or (base_receipt.provider if base_receipt is not None else ""))
            candidate_provider = str(cls._value(candidate, "provider", "") or (candidate_receipt.provider if candidate_receipt is not None else ""))
            if not base_provider or not candidate_provider or base_provider != candidate_provider:
                mismatched.append("provider")
            for row, label in ((base, baseline_arm), (candidate, candidate_arm)):
                if bool(cls._value(row, "usage_receipt_hash", "")) and not str(cls._value(row, "arm_version", "")):
                    mismatched.append(f"arm_version:{label}")
            if mismatched:
                identity_mismatches.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "fields": list(dict.fromkeys(mismatched))})

            effective_quota: dict[str, float | None] = {}
            for arm, row in ((baseline_arm, base), (candidate_arm, candidate)):
                quota = cls._value(row, "quota_cost")
                receipt = receipt_index.get((task_id, repetition, cache_mode, arm))
                if receipt is not None:
                    reasons = receipt.validate()
                    row_bindings = {
                        "provider": receipt.provider,
                        "request_id_hash": receipt.request_id_hash,
                        "provider_response_hash": receipt.provider_response_hash,
                        "hardware_hash": receipt.hardware_hash,
                        "fresh_input_tokens": receipt.fresh_input_tokens,
                        "cached_input_tokens": receipt.cached_input_tokens,
                        "output_tokens": receipt.output_tokens,
                        "reasoning_tokens": receipt.reasoning_tokens,
                    }
                    strict_row = bool(cls._value(row, "usage_receipt_hash", ""))
                    for field, expected in row_bindings.items():
                        actual = cls._value(row, field, None)
                        missing = actual is None or actual == ""
                        if strict_row:
                            if missing or actual != expected:
                                reasons.append(f"receipt-row-mismatch:{field}")
                        elif not missing and actual != expected:
                            reasons.append(f"receipt-row-mismatch:{field}")
                    try:
                        if float(cls._value(row, "quota_cost")) != float(receipt.quota_cost):
                            reasons.append("receipt-row-mismatch:quota_cost")
                    except (TypeError, ValueError):
                        reasons.append("receipt-row-mismatch:quota_cost")
                    if reasons:
                        receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": list(dict.fromkeys(reasons))})
                    else:
                        quota = receipt.quota_cost
                elif require_receipts and not legacy_provider_row(row):
                    receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": ["receipt-missing"]})
                try:
                    numeric_quota = float(quota) if quota is not None else 0.0
                except (TypeError, ValueError, OverflowError):
                    numeric_quota = 0.0
                if not math.isfinite(numeric_quota) or numeric_quota <= 0:
                    invalid.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reason": "quota-unavailable"})
                    effective_quota[arm] = None
                else:
                    effective_quota[arm] = numeric_quota
                    totals[arm]["quota"] += numeric_quota

            base_success = bool(cls._value(base, "success", False) and cls._value(base, "verifier_success", False))
            candidate_success = bool(cls._value(candidate, "success", False) and cls._value(candidate, "verifier_success", False))
            equal_work = float(cls._value(base, "verified_work", 0.0) or 0.0) == float(cls._value(candidate, "verified_work", 0.0) or 0.0)
            if base_success and candidate_success and equal_work and effective_quota.get(baseline_arm) and effective_quota.get(candidate_arm):
                ratios.append(float(effective_quota[baseline_arm]) / float(effective_quota[candidate_arm]))

        pass_rates = {arm: bucket["successes"] / bucket["attempts"] if bucket["attempts"] else 0.0 for arm, bucket in totals.items()}
        utility = {arm: bucket["work"] / bucket["quota"] if bucket["quota"] > 0 else 0.0 for arm, bucket in totals.items()}
        aggregate_ratio = utility[candidate_arm] / utility[baseline_arm] if utility[baseline_arm] > 0 else 0.0
        ratios.sort()
        ci = _bootstrap_ci(ratios)
        median = ratios[len(ratios) // 2] if ratios else None
        legacy_unversioned_arms = set()
        for legacy_arm in (baseline_arm, candidate_arm):
            arm_rows = [row for (task, repetition, cache, arm), row in keyed.items() if arm == legacy_arm]
            if arm_rows and all(
                not (row.get("usage_receipt_hash", "") if isinstance(row, Mapping) else getattr(row, "usage_receipt_hash", ""))
                and not (row.get("arm_version", "") if isinstance(row, Mapping) else getattr(row, "arm_version", ""))
                for row in arm_rows
            ):
                legacy_unversioned_arms.add(legacy_arm)
        identity_mismatches = [
            item for item in identity_mismatches
            if not (
                item.get("scope") == "arm-global"
                and item.get("arm") in legacy_unversioned_arms
                and item.get("fields") == ["arm_version"]
            )
        ]
        claimable = bool(
            matched_pairs >= minimum_pairs
            and len(ratios) >= minimum_pairs
            and ci
            and ci[0] > 1
            and aggregate_ratio > 1
            and pass_rates[candidate_arm] >= pass_rates[baseline_arm]
            and not invalid
            and not identity_mismatches
            and not receipt_errors
            and totals[candidate_arm]["security"] == 0
            and totals[candidate_arm]["skips"] == 0
        )
        return {
            "schema_version": 1,
            "baseline": baseline_arm,
            "candidate": candidate_arm,
            "matched_pairs": matched_pairs,
            "successful_equal_work_pairs": len(ratios),
            "median_success_pair_ratio": median,
            "confidence_interval_95": ci,
            "pass_rates": pass_rates,
            "total_verified_work": {arm: totals[arm]["work"] for arm in totals},
            "total_quota": {arm: totals[arm]["quota"] for arm in totals},
            "verified_work_per_quota": utility,
            "failure_inclusive_efficiency_ratio": aggregate_ratio,
            "identity_mismatches": identity_mismatches,
            "receipt_errors": receipt_errors,
            "invalid": invalid,
            "claimable_superiority": claimable,
            "claim": "SUPERIORITY_PROVEN" if claimable else "NOT_PROVEN",
        }
