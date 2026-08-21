from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .runtime_evidence import RuntimeEvidenceGraph
from .signalbench_hardened import UsageReceipt
from .token_attribution import TokenAttributionReceipt
from .util import canonical_json, sha256_bytes


DECISION_KINDS: tuple[str, ...] = ("context", "tool", "policy")


def _finite_non_negative(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _sha256(value: str, name: str) -> str:
    normalized = str(value).casefold()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be lowercase sha256")
    return normalized


@dataclass(frozen=True)
class PerformanceBudget:
    max_latency_ms: float
    max_cpu_ms: float
    max_peak_memory_bytes: int
    max_disk_write_bytes: int
    max_token_overhead: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_latency_ms", _finite_non_negative(self.max_latency_ms, "max_latency_ms"))
        object.__setattr__(self, "max_cpu_ms", _finite_non_negative(self.max_cpu_ms, "max_cpu_ms"))
        for name in ("max_peak_memory_bytes", "max_disk_write_bytes", "max_token_overhead"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class RecoveryBudget:
    max_amplification: float
    require_exact_recovery: bool = True

    def __post_init__(self) -> None:
        value = float(self.max_amplification)
        if not math.isfinite(value) or value < 1.0:
            raise ValueError("max_amplification must be finite and >= 1")
        object.__setattr__(self, "max_amplification", value)


@dataclass(frozen=True)
class QualitySLO:
    min_task_success_rate: float
    min_verifier_success_rate: float
    min_critical_evidence_rate: float
    max_unsafe_actions: int = 0

    def __post_init__(self) -> None:
        for name in ("min_task_success_rate", "min_verifier_success_rate", "min_critical_evidence_rate"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
        unsafe = int(self.max_unsafe_actions)
        if unsafe < 0:
            raise ValueError("max_unsafe_actions must be non-negative")
        object.__setattr__(self, "max_unsafe_actions", unsafe)


@dataclass(frozen=True)
class AttributionPolicy:
    performance: PerformanceBudget
    recovery: RecoveryBudget
    quality: QualitySLO

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def snapshot_hash(self) -> str:
        return sha256_bytes(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class PerformanceSample:
    latency_ms: float
    cpu_ms: float
    peak_memory_bytes: int
    disk_write_bytes: int
    token_overhead: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "latency_ms", _finite_non_negative(self.latency_ms, "latency_ms"))
        object.__setattr__(self, "cpu_ms", _finite_non_negative(self.cpu_ms, "cpu_ms"))
        for name in ("peak_memory_bytes", "disk_write_bytes", "token_overhead"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class RecoverySample:
    requested_units: int
    recovered_units: int
    exact_recovery: bool

    def __post_init__(self) -> None:
        requested = int(self.requested_units)
        recovered = int(self.recovered_units)
        if requested <= 0:
            raise ValueError("requested_units must be positive")
        if recovered < requested:
            raise ValueError("recovered_units cannot be lower than requested_units")
        object.__setattr__(self, "requested_units", requested)
        object.__setattr__(self, "recovered_units", recovered)

    @property
    def amplification(self) -> float:
        return self.recovered_units / self.requested_units


@dataclass(frozen=True)
class QualitySample:
    task_success: bool
    verifier_success: bool
    critical_evidence_complete: bool
    unsafe_actions: int = 0

    def __post_init__(self) -> None:
        value = int(self.unsafe_actions)
        if value < 0:
            raise ValueError("unsafe_actions must be non-negative")
        object.__setattr__(self, "unsafe_actions", value)


@dataclass(frozen=True)
class GateEvaluation:
    ok: bool
    policy_hash: str
    performance_reasons: tuple[str, ...]
    recovery_reasons: tuple[str, ...]
    quality_reasons: tuple[str, ...]
    metrics: dict[str, Any]
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionAttributionReceipt:
    receipt_id: str
    task_id: str
    session_id: str
    decision_kind: str
    action: str
    subject: str
    policy_hash: str
    evidence_hashes: tuple[str, ...]
    usage_receipt_hash: str
    token_attribution_receipt_hash: str
    request_id_hash: str
    provider: str
    model: str
    gate_receipt_hash: str
    repository_commit: str
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservabilityAttribution:
    """Decision attribution composed over existing Syntavra evidence authorities.

    This layer owns no parallel persistence. Decision relationships are written
    into the supplied RuntimeEvidenceGraph. Provider usage and token attribution
    remain owned by their existing append-only ledgers.
    """

    schema_version = 1
    source = "observability-attribution"

    def __init__(self, graph: RuntimeEvidenceGraph):
        self.graph = graph

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "context_decision_attribution": True,
            "tool_decision_attribution": True,
            "policy_decision_attribution": True,
            "deterministic_policy_snapshot": True,
            "provider_usage_receipt_linkage": True,
            "token_attribution_receipt_linkage": True,
            "performance_budget_gate": True,
            "recovery_amplification_gate": True,
            "context_quality_slo_gate": True,
            "runtime_evidence_graph_reused": True,
            "parallel_persistent_store": False,
            "provider_usage_store_duplicated": False,
            "token_attribution_store_duplicated": False,
            "public_cli_route": False,
        }

    @staticmethod
    def policy_snapshot(policy: AttributionPolicy | Mapping[str, Any]) -> str:
        payload = policy.to_dict() if isinstance(policy, AttributionPolicy) else dict(policy)
        return sha256_bytes(canonical_json(payload))

    @classmethod
    def evaluate(
        cls,
        *,
        policy: AttributionPolicy,
        performance: PerformanceSample,
        recovery: RecoverySample,
        quality_samples: Sequence[QualitySample],
    ) -> GateEvaluation:
        samples = tuple(quality_samples)
        if not samples:
            raise ValueError("quality_samples cannot be empty")

        performance_reasons: list[str] = []
        if performance.latency_ms > policy.performance.max_latency_ms:
            performance_reasons.append("latency-budget-exceeded")
        if performance.cpu_ms > policy.performance.max_cpu_ms:
            performance_reasons.append("cpu-budget-exceeded")
        if performance.peak_memory_bytes > policy.performance.max_peak_memory_bytes:
            performance_reasons.append("memory-budget-exceeded")
        if performance.disk_write_bytes > policy.performance.max_disk_write_bytes:
            performance_reasons.append("disk-budget-exceeded")
        if performance.token_overhead > policy.performance.max_token_overhead:
            performance_reasons.append("token-overhead-budget-exceeded")

        recovery_reasons: list[str] = []
        if policy.recovery.require_exact_recovery and not recovery.exact_recovery:
            recovery_reasons.append("exact-recovery-required")
        if recovery.amplification > policy.recovery.max_amplification:
            recovery_reasons.append("recovery-amplification-exceeded")

        count = len(samples)
        task_rate = sum(int(item.task_success) for item in samples) / count
        verifier_rate = sum(int(item.verifier_success) for item in samples) / count
        evidence_rate = sum(int(item.critical_evidence_complete) for item in samples) / count
        unsafe_actions = sum(item.unsafe_actions for item in samples)

        quality_reasons: list[str] = []
        if task_rate < policy.quality.min_task_success_rate:
            quality_reasons.append("task-success-slo-violated")
        if verifier_rate < policy.quality.min_verifier_success_rate:
            quality_reasons.append("verifier-success-slo-violated")
        if evidence_rate < policy.quality.min_critical_evidence_rate:
            quality_reasons.append("critical-evidence-slo-violated")
        if unsafe_actions > policy.quality.max_unsafe_actions:
            quality_reasons.append("unsafe-action-slo-violated")

        metrics = {
            "performance": asdict(performance),
            "recovery": {**asdict(recovery), "amplification": recovery.amplification},
            "quality": {
                "samples": count,
                "task_success_rate": task_rate,
                "verifier_success_rate": verifier_rate,
                "critical_evidence_rate": evidence_rate,
                "unsafe_actions": unsafe_actions,
            },
        }
        body = {
            "schema_version": cls.schema_version,
            "policy_hash": policy.snapshot_hash,
            "performance_reasons": sorted(performance_reasons),
            "recovery_reasons": sorted(recovery_reasons),
            "quality_reasons": sorted(quality_reasons),
            "metrics": metrics,
        }
        receipt_hash = sha256_bytes(canonical_json(body))
        return GateEvaluation(
            ok=not (performance_reasons or recovery_reasons or quality_reasons),
            policy_hash=policy.snapshot_hash,
            performance_reasons=tuple(sorted(performance_reasons)),
            recovery_reasons=tuple(sorted(recovery_reasons)),
            quality_reasons=tuple(sorted(quality_reasons)),
            metrics=metrics,
            receipt_hash=receipt_hash,
        )

    @staticmethod
    def _verify_usage_link(usage_receipt: UsageReceipt, token_receipt: TokenAttributionReceipt) -> None:
        reasons = usage_receipt.validate()
        if reasons:
            raise ValueError("invalid provider usage receipt: " + ",".join(reasons))
        _sha256(usage_receipt.receipt_hash, "usage_receipt.receipt_hash")
        _sha256(token_receipt.receipt_hash, "token_receipt.receipt_hash")
        if token_receipt.provider_receipt_hash != usage_receipt.receipt_hash:
            raise ValueError("token attribution does not reference provider usage receipt")
        if token_receipt.request_id_hash != usage_receipt.request_id_hash:
            raise ValueError("token attribution request id does not match provider usage receipt")
        if token_receipt.provider.casefold() != usage_receipt.provider.casefold():
            raise ValueError("token attribution provider does not match provider usage receipt")

    def record_decision(
        self,
        *,
        task_id: str,
        session_id: str,
        decision_kind: str,
        action: str,
        subject: str,
        policy: AttributionPolicy,
        evidence_hashes: Sequence[str] = (),
        usage_receipt: UsageReceipt | None = None,
        token_receipt: TokenAttributionReceipt | None = None,
        gate: GateEvaluation | None = None,
        repository_commit: str = "unknown",
    ) -> DecisionAttributionReceipt:
        task = str(task_id).strip()
        session = str(session_id).strip()
        kind = str(decision_kind).strip().casefold()
        selected_action = str(action).strip()
        selected_subject = str(subject).strip()
        if not task or not session:
            raise ValueError("decision attribution task/session identity is incomplete")
        if kind not in DECISION_KINDS:
            raise ValueError(f"unknown decision kind: {kind}")
        if not selected_action or not selected_subject:
            raise ValueError("decision attribution action/subject is incomplete")
        if (usage_receipt is None) != (token_receipt is None):
            raise ValueError("provider usage and token attribution receipts must be supplied together")
        if gate is not None and gate.policy_hash != policy.snapshot_hash:
            raise ValueError("gate policy hash does not match decision policy snapshot")
        if usage_receipt is not None and token_receipt is not None:
            self._verify_usage_link(usage_receipt, token_receipt)

        normalized_evidence = tuple(sorted({_sha256(value, "evidence_hash") for value in evidence_hashes}))
        usage_hash = usage_receipt.receipt_hash if usage_receipt is not None else ""
        token_hash = token_receipt.receipt_hash if token_receipt is not None else ""
        request_hash = usage_receipt.request_id_hash if usage_receipt is not None else ""
        provider = usage_receipt.provider if usage_receipt is not None else ""
        model = token_receipt.model if token_receipt is not None else ""
        gate_hash = gate.receipt_hash if gate is not None else ""

        body = {
            "schema_version": self.schema_version,
            "task_id": task,
            "session_id": session,
            "decision_kind": kind,
            "action": selected_action,
            "subject": selected_subject,
            "policy_hash": policy.snapshot_hash,
            "evidence_hashes": normalized_evidence,
            "usage_receipt_hash": usage_hash,
            "token_attribution_receipt_hash": token_hash,
            "request_id_hash": request_hash,
            "provider": provider,
            "model": model,
            "gate_receipt_hash": gate_hash,
            "repository_commit": repository_commit,
        }
        receipt_hash = sha256_bytes(canonical_json(body))
        receipt = DecisionAttributionReceipt(
            receipt_id=f"obs-{receipt_hash[:24]}", task_id=task, session_id=session,
            decision_kind=kind, action=selected_action, subject=selected_subject,
            policy_hash=policy.snapshot_hash, evidence_hashes=normalized_evidence,
            usage_receipt_hash=usage_hash, token_attribution_receipt_hash=token_hash,
            request_id_hash=request_hash, provider=provider, model=model,
            gate_receipt_hash=gate_hash, repository_commit=repository_commit,
            receipt_hash=receipt_hash,
        )

        task_node = self.graph.put_node(kind="task", label=task, source=self.source, repository_commit=repository_commit)
        session_node = self.graph.put_node(kind="session", label=session, source=self.source, repository_commit=repository_commit)
        policy_node = self.graph.put_node(kind="policy-snapshot", label=policy.snapshot_hash, source=self.source, repository_commit=repository_commit)
        decision_node = self.graph.put_node(
            kind=f"{kind}-decision", label=receipt.receipt_hash, source=self.source,
            repository_commit=repository_commit,
            metadata={"receipt_id": receipt.receipt_id, "action": selected_action, "subject": selected_subject},
        )
        self.graph.put_edge(task_node.node_id, session_node.node_id, "HAS_SESSION", repository_commit=repository_commit)
        self.graph.put_edge(task_node.node_id, decision_node.node_id, "ATTRIBUTED_DECISION", repository_commit=repository_commit, metadata={"decision_kind": kind})
        self.graph.put_edge(decision_node.node_id, policy_node.node_id, "USED_POLICY", repository_commit=repository_commit)

        for evidence_hash in normalized_evidence:
            evidence_node = self.graph.put_node(kind="evidence-receipt", label=evidence_hash, source=self.source, repository_commit=repository_commit)
            self.graph.put_edge(decision_node.node_id, evidence_node.node_id, "USED_EVIDENCE", repository_commit=repository_commit)

        if gate is not None:
            gate_node = self.graph.put_node(kind="quality-budget-gate", label=gate.receipt_hash, source=self.source, repository_commit=repository_commit, metadata={"ok": gate.ok})
            self.graph.put_edge(decision_node.node_id, gate_node.node_id, "EVALUATED_BY", repository_commit=repository_commit)

        if usage_receipt is not None and token_receipt is not None:
            usage_node = self.graph.put_node(kind="provider-usage-receipt", label=usage_receipt.receipt_hash, source="provider-usage-ledger", repository_commit=repository_commit)
            token_node = self.graph.put_node(kind="token-attribution-receipt", label=token_receipt.receipt_hash, source="token-attribution-ledger", repository_commit=repository_commit)
            self.graph.put_edge(decision_node.node_id, usage_node.node_id, "LINKED_PROVIDER_USAGE", repository_commit=repository_commit)
            self.graph.put_edge(decision_node.node_id, token_node.node_id, "LINKED_TOKEN_ATTRIBUTION", repository_commit=repository_commit)
            self.graph.put_edge(token_node.node_id, usage_node.node_id, "ATTRIBUTES_PROVIDER_USAGE", repository_commit=repository_commit)

        return receipt


__all__ = [
    "AttributionPolicy", "DECISION_KINDS", "DecisionAttributionReceipt", "GateEvaluation",
    "ObservabilityAttribution", "PerformanceBudget", "PerformanceSample", "QualitySLO",
    "QualitySample", "RecoveryBudget", "RecoverySample",
]
