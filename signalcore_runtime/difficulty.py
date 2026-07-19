from __future__ import annotations

import math
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


def evaluate_difficulty(tier: str, axes: Mapping[str, float], *, integrity: Mapping[str, bool] | None = None) -> DifficultyResult:
    if tier not in TIER_RULES:
        raise ValueError(f"unknown tier: {tier}")
    normalized = {axis: float(axes.get(axis, 0.0)) for axis in AXES}
    errors = [f"invalid-axis:{axis}" for axis, value in normalized.items() if value <= 0 or not math.isfinite(value)]
    safe = {axis: max(0.01, min(value, 1000.0)) for axis, value in normalized.items()}
    geometric = math.exp(sum(math.log(value) for value in safe.values()) / len(safe))
    harmonic = len(safe) / sum(1.0 / value for value in safe.values())
    critical_floor = min(safe[axis] for axis in CRITICAL)
    score = 1.0 if tier == "1X" else geometric * (harmonic / geometric) ** 0.35 * min(1.5, math.sqrt(critical_floor / max(geometric, 0.01)) + 0.5)
    rule = TIER_RULES[tier]
    checks = {
        "score": score >= rule["score"],
        "multi_axis_participation": sum(value >= rule["participation_floor"] for value in safe.values()) >= rule["participation_count"],
        "critical_high": sum(safe[axis] >= rule["critical_high"] for axis in CRITICAL) >= rule["critical_count"],
        "critical_floor": all(safe[axis] >= rule["critical_floor"] for axis in CRITICAL),
    }
    for name, passed in (integrity or {}).items():
        checks[f"integrity:{name}"] = bool(passed)
        if not passed:
            errors.append(f"integrity-failed:{name}")
    qualified = not errors and all(checks.values())
    return DifficultyResult(tier, score, normalized, checks, qualified, tuple(errors))
