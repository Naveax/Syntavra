from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import ClaimDecision, DifficultyResult
from .util import atomic_write_json, canonical_json, sha256_bytes, sha256_file


CLAIM_MAP = {
    "20X": "5X_20X_QUALIFIED",
    "30X": "5X_30X_ENDURANCE_QUALIFIED",
    "100X": "5X_100X_ABSOLUTE_QUALIFIED",
}


def bootstrap_ci(
    values: list[float],
    *,
    iterations: int = 10000,
    confidence: float = 0.95,
    seed: int = 1337,
) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    medians = [statistics.median(rng.choice(values) for _ in values) for _ in range(iterations)]
    medians.sort()
    alpha = (1 - confidence) / 2
    low = medians[max(0, int(alpha * len(medians)))]
    high = medians[min(len(medians) - 1, int((1 - alpha) * len(medians)))]
    return low, high


def decide_claim(
    *,
    tier: str,
    baseline_costs: Iterable[float],
    signalcore_costs: Iterable[float],
    difficulty: DifficultyResult,
    success_rate_regression: float = 0.0,
    required_verifier_skips: int = 0,
    security_regressions: int = 0,
    stale_evidence_errors: int = 0,
    recovery_failures: int = 0,
    integrity_violations: int = 0,
    actual_quota_available: bool = True,
    minimum_pairs: int = 10,
) -> ClaimDecision:
    baseline = list(baseline_costs)
    signalcore = list(signalcore_costs)
    reasons: list[str] = []
    if not actual_quota_available:
        reasons.append("actual-quota-unavailable")
    if len(baseline) != len(signalcore) or not baseline:
        reasons.append("invalid-paired-sample-count")
    if len(baseline) < minimum_pairs:
        reasons.append(f"insufficient-valid-pairs:{len(baseline)}<{minimum_pairs}")
    ratios = [base / signal for base, signal in zip(baseline, signalcore) if base > 0 and signal > 0]
    if len(ratios) != len(baseline):
        reasons.append("nonpositive-cost")
    median = statistics.median(ratios) if ratios else None
    geometric = math.exp(sum(math.log(value) for value in ratios) / len(ratios)) if ratios else None
    ci = bootstrap_ci(ratios) if ratios else None
    if not difficulty.observed:
        reasons.append("difficulty-not-observed")
    if not difficulty.qualified:
        reasons.append("difficulty-not-qualified")
    if median is None or median < 5.0:
        reasons.append("median-below-5x")
    if geometric is None or geometric < 5.0:
        reasons.append("geometric-mean-below-5x")
    if ci is None or ci[0] < 5.0:
        reasons.append("confidence-lower-bound-below-5x")
    if success_rate_regression > 0:
        reasons.append("success-rate-regression")
    if required_verifier_skips:
        reasons.append("required-verifier-skipped")
    if security_regressions:
        reasons.append("security-regression")
    if stale_evidence_errors:
        reasons.append("stale-evidence-error")
    if recovery_failures:
        reasons.append("recovery-failure")
    if integrity_violations:
        reasons.append("benchmark-integrity-violation")
    claim = CLAIM_MAP.get(tier, "5X_BASELINE_PROVEN") if not reasons else "5X_NOT_PROVEN"
    payload = {
        "tier": tier,
        "difficulty": asdict(difficulty),
        "baseline": baseline,
        "signalcore": signalcore,
        "ratios": ratios,
        "median": median,
        "geometric": geometric,
        "ci": ci,
        "minimum_pairs": minimum_pairs,
        "reasons": sorted(set(reasons)),
    }
    return ClaimDecision(
        claim,
        "PASS" if claim != "5X_NOT_PROVEN" else "NOT_PROVEN",
        difficulty.score,
        median,
        geometric,
        ci,
        tuple(sorted(set(reasons))),
        "sha256:" + sha256_bytes(canonical_json(payload)),
    )


def write_claim(path: Path, decision: ClaimDecision, *, artifacts: dict[str, Path] | None = None) -> dict:
    value = asdict(decision)
    value["schema_version"] = 2
    value["artifact_hashes"] = {name: sha256_file(file) for name, file in sorted((artifacts or {}).items())}
    value["receipt_hash"] = sha256_bytes(canonical_json(value))
    atomic_write_json(path, value, mode=0o644)
    return value


def verify_claim(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    saved = value.pop("receipt_hash", None)
    reasons: list[str] = []
    if saved != sha256_bytes(canonical_json(value)):
        reasons.append("receipt-hash-mismatch")
    for name, digest in value.get("artifact_hashes", {}).items():
        candidate = path.parent / name
        if not candidate.is_file() or sha256_file(candidate) != digest:
            reasons.append(f"artifact-invalid:{name}")
    if value.get("status") == "PASS" and value.get("claim") == "5X_NOT_PROVEN":
        reasons.append("contradictory-status")
    return {"ok": not reasons, "reasons": reasons, "claim": value.get("claim"), "status": value.get("status")}
