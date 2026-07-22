from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReadinessEvidence:
    host_interception_coverage: float
    real_repository_tasks: int
    competitor_arms: int
    valid_paired_repetitions: int
    provider_receipt_coverage: float
    semantic_recall_at_5: float
    temporal_truth_accuracy: float
    concurrency_success_rate: float
    exact_roundtrip_rate: float
    security_regressions: int
    pass_rate_delta: float
    p95_latency_ms: float


@dataclass(frozen=True)
class GateResult:
    score: float
    grade: str
    ten_of_ten: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    evidence: dict[str, Any]


class SyntavraReadinessGate:
    """Fail-closed 10/10 gate. It cannot be satisfied by internal byte reduction alone."""

    gates = {
        "host-interception": lambda e: e.host_interception_coverage >= 0.95,
        "real-task-corpus": lambda e: e.real_repository_tasks >= 50,
        "competitor-arms": lambda e: e.competitor_arms >= 3,
        "paired-repetitions": lambda e: e.valid_paired_repetitions >= 30,
        "provider-receipts": lambda e: e.provider_receipt_coverage >= 0.99,
        "semantic-recall": lambda e: e.semantic_recall_at_5 >= 0.90,
        "temporal-truth": lambda e: e.temporal_truth_accuracy >= 0.95,
        "concurrency": lambda e: e.concurrency_success_rate >= 0.999,
        "exact-roundtrip": lambda e: e.exact_roundtrip_rate == 1.0,
        "zero-security-regression": lambda e: e.security_regressions == 0,
        "no-pass-rate-regression": lambda e: e.pass_rate_delta >= 0.0,
        "latency": lambda e: e.p95_latency_ms <= 250.0,
    }

    @classmethod
    def evaluate(cls, evidence: ReadinessEvidence | Mapping[str, Any]) -> GateResult:
        value = evidence if isinstance(evidence, ReadinessEvidence) else ReadinessEvidence(**dict(evidence))
        passed = tuple(name for name, predicate in cls.gates.items() if predicate(value))
        failed = tuple(name for name in cls.gates if name not in passed)
        score = round(10.0 * len(passed) / len(cls.gates), 2)
        ten = not failed
        grade = "10/10" if ten else f"{score:.2f}/10"
        return GateResult(score, grade, ten, passed, failed, asdict(value))
