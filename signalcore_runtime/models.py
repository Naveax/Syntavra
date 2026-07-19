from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ACTIVATION_STATES = {
    "NOT_INSTALLED",
    "INSTRUCTION_ONLY",
    "RUNTIME_PARTIAL",
    "RUNTIME_ACTIVE",
    "RUNTIME_DEGRADED",
    "RUNTIME_FAILED",
}


@dataclass(frozen=True)
class RuntimeHealth:
    state: str
    healthy: bool
    checks: dict[str, bool]
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in ACTIVATION_STATES:
            raise ValueError(f"invalid activation state: {self.state}")


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    state: str
    argv: tuple[str, ...]
    cwd: str
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    pid: int | None = None
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    summary: str = ""
    evidence_handle: str = ""
    error: str = ""


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    cancelled: bool
    summary: str
    evidence_handle: str
    stdout_bytes: int
    stderr_bytes: int


@dataclass(frozen=True)
class ContextDecision:
    utilization: float
    level: int
    actions: tuple[str, ...]
    mandatory_split: bool


@dataclass(frozen=True)
class DifficultyResult:
    tier: str
    score: float
    axes: dict[str, float]
    checks: dict[str, bool]
    qualified: bool
    integrity_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimDecision:
    claim: str
    status: str
    difficulty_score: float
    median_ratio: float | None
    geometric_mean_ratio: float | None
    confidence_interval_95: tuple[float, float] | None
    reasons: tuple[str, ...]
    evidence_receipt: str
