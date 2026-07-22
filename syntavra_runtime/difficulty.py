from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .models import DifficultyResult


AXES = ("R", "C", "O", "T", "P", "V", "X", "H", "S", "F")
CRITICAL = ("R", "C", "O", "T", "V")
TIER_RULES = {
    "1X": {"score": 0.0, "participation_count": 0, "participation_floor": 0.0, "critical_count": 0, "critical_high": 0.0, "critical_floor": 0.0},
    "20X": {"score": 20.0, "participation_count": 5, "participation_floor": 5.0, "critical_count": 3, "critical_high": 10.0, "critical_floor": 2.0},
    "30X": {"score": 30.0, "participation_count": 6, "participation_floor": 7.5, "critical_count": 3, "critical_high": 15.0, "critical_floor": 3.0},
    "100X": {"score": 100.0, "participation_count": 7, "participation_floor": 20.0, "critical_count": 4, "critical_high": 50.0, "critical_floor": 5.0},
}


@dataclass(frozen=True)
class ObservedWorkload:
    raw: dict[str, float]
    baseline: dict[str, float]
    factors: dict[str, float]


def _score_axes(tier: str, axes: Mapping[str, float], integrity: Mapping[str, bool] | None, *, observed: bool) -> DifficultyResult:
    if tier not in TIER_RULES:
        raise ValueError(f"unknown tier: {tier}")
    normalized = {axis: float(axes.get(axis, 0.0)) for axis in AXES}
    errors = [f"invalid-axis:{axis}" for axis, value in normalized.items() if value <= 0 or not math.isfinite(value)]
    safe = {axis: max(0.01, min(value, 1000.0)) for axis, value in normalized.items()}
    geometric = math.exp(sum(math.log(value) for value in safe.values()) / len(safe))
    harmonic = len(safe) / sum(1.0 / value for value in safe.values())
    critical_floor = min(safe[axis] for axis in CRITICAL)
    score = 1.0 if tier == "1X" else geometric * (harmonic / geometric) ** 0.35 * min(
        1.5,
        math.sqrt(critical_floor / max(geometric, 0.01)) + 0.5,
    )
    rule = TIER_RULES[tier]
    checks = {
        "score": score >= rule["score"],
        "multi_axis_participation": sum(value >= rule["participation_floor"] for value in safe.values()) >= rule["participation_count"],
        "critical_high": sum(safe[axis] >= rule["critical_high"] for axis in CRITICAL) >= rule["critical_count"],
        "critical_floor": all(safe[axis] >= rule["critical_floor"] for axis in CRITICAL),
        "observed_measurement": observed or tier == "1X",
    }
    for name, passed in (integrity or {}).items():
        checks[f"integrity:{name}"] = bool(passed)
        if not passed:
            errors.append(f"integrity-failed:{name}")
    if not observed and tier != "1X":
        errors.append("difficulty-is-configured-not-observed")
    qualified = not errors and all(checks.values())
    return DifficultyResult(tier, score, normalized, checks, qualified, tuple(errors), observed)


def evaluate_difficulty(
    tier: str,
    axes: Mapping[str, float],
    *,
    integrity: Mapping[str, bool] | None = None,
) -> DifficultyResult:
    """Evaluate configured factors.

    Config qualification is useful for workload construction, but deliberately
    cannot qualify a public performance claim. Use ``evaluate_observed`` after an
    actual run for claim-bearing evidence.
    """
    return _score_axes(tier, axes, integrity, observed=False)


def observe_workload(raw: Mapping[str, float], baseline: Mapping[str, float]) -> ObservedWorkload:
    missing = [axis for axis in AXES if axis not in raw or axis not in baseline]
    if missing:
        raise ValueError("missing observed axes: " + ",".join(missing))
    factors: dict[str, float] = {}
    for axis in AXES:
        value = float(raw[axis])
        unit = float(baseline[axis])
        if value <= 0 or unit <= 0 or not math.isfinite(value) or not math.isfinite(unit):
            raise ValueError(f"invalid observed axis: {axis}")
        factors[axis] = value / unit
    return ObservedWorkload(dict(raw), dict(baseline), factors)


def evaluate_observed(
    tier: str,
    raw: Mapping[str, float],
    baseline: Mapping[str, float],
    *,
    integrity: Mapping[str, bool] | None = None,
) -> DifficultyResult:
    observed = observe_workload(raw, baseline)
    return _score_axes(tier, observed.factors, integrity, observed=True)


def evaluate_configured(
    tier: str,
    axes: Mapping[str, float],
    *,
    integrity: Mapping[str, bool] | None = None,
) -> DifficultyResult:
    """Validate workload construction without upgrading it to observed evidence."""
    measured_shape = _score_axes(tier, axes, integrity, observed=True)
    return DifficultyResult(
        measured_shape.tier,
        measured_shape.score,
        measured_shape.axes,
        measured_shape.checks,
        measured_shape.qualified,
        measured_shape.integrity_errors,
        False,
    )
