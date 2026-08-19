from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .context_pack import TaskContextPack
from .optimization_modes import OptimizationMode
from .util import canonical_json, sha256_bytes


ITEM_ACTIONS = frozenset({"KEEP", "SUMMARIZE", "COMPRESS", "EXTERNALIZE", "ABSTAIN"})
SESSION_ACTIONS = frozenset({"KEEP", "RESET", "BRANCH", "ABSTAIN"})
_FORBIDDEN_METADATA_KEYS = frozenset({"content", "payload", "raw_text", "body", "secret", "text"})


def _bounded(value: float, *, name: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be within [0,1]")
    return number


def _metadata_is_reference_only(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_METADATA_KEYS:
                raise ValueError(f"{path} cannot carry context payload authority: {key}")
            _metadata_is_reference_only(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _metadata_is_reference_only(nested, path=f"{path}[{index}]")


@dataclass(frozen=True)
class ContextPolicySignal:
    identity: str
    token_count: int
    relevance: float
    trust: float = 1.0
    freshness: float = 1.0
    recoverable: bool = True
    exact_required: bool = False
    tainted: bool = False
    security_denied: bool = False
    summarizable: bool = True
    compressible: bool = True
    externalizable: bool = True
    namespace_uri: str = ""
    item_id: str = ""
    source_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("context policy signal requires identity")
        if int(self.token_count) < 0:
            raise ValueError("token_count must be non-negative")
        _bounded(self.relevance, name="relevance")
        _bounded(self.trust, name="trust")
        _bounded(self.freshness, name="freshness")
        _metadata_is_reference_only(self.metadata)
        object.__setattr__(self, "token_count", int(self.token_count))
        object.__setattr__(
            self,
            "source_refs",
            tuple(sorted(set(str(value) for value in self.source_refs if str(value)))),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class ContextPolicyState:
    current_context_tokens: int = 0
    unresolved_critical_evidence: int = 0
    irreversible_action_pending: bool = False
    task_drift: float = 0.0
    branch_allowed: bool = True
    reset_allowed: bool = True
    shadow_mode: bool = False

    def __post_init__(self) -> None:
        if int(self.current_context_tokens) < 0:
            raise ValueError("current_context_tokens must be non-negative")
        if int(self.unresolved_critical_evidence) < 0:
            raise ValueError("unresolved_critical_evidence must be non-negative")
        _bounded(self.task_drift, name="task_drift")
        object.__setattr__(self, "current_context_tokens", int(self.current_context_tokens))
        object.__setattr__(
            self,
            "unresolved_critical_evidence",
            int(self.unresolved_critical_evidence),
        )


@dataclass(frozen=True)
class AdaptivePolicyConfig:
    context_budget_tokens: int
    target_utilization: float = 0.82
    reset_threshold: float = 0.96
    branch_threshold: float = 0.72
    keep_threshold: float = 0.78
    summarize_threshold: float = 0.58
    compress_threshold: float = 0.36
    external_reference_tokens: int = 24

    def __post_init__(self) -> None:
        if int(self.context_budget_tokens) < 256:
            raise ValueError("context_budget_tokens must be at least 256")
        for name in (
            "target_utilization",
            "reset_threshold",
            "branch_threshold",
            "keep_threshold",
            "summarize_threshold",
            "compress_threshold",
        ):
            _bounded(getattr(self, name), name=name)
        if self.target_utilization > self.reset_threshold:
            raise ValueError("target_utilization cannot exceed reset_threshold")
        if not self.keep_threshold >= self.summarize_threshold >= self.compress_threshold:
            raise ValueError("item decision thresholds must be monotonic")
        if int(self.external_reference_tokens) < 1:
            raise ValueError("external_reference_tokens must be positive")
        object.__setattr__(self, "context_budget_tokens", int(self.context_budget_tokens))
        object.__setattr__(self, "external_reference_tokens", int(self.external_reference_tokens))

    @classmethod
    def from_optimization_mode(cls, mode: OptimizationMode) -> "AdaptivePolicyConfig":
        return cls(context_budget_tokens=max(256, int(mode.context_budget_tokens)))


@dataclass(frozen=True)
class ContextPolicyDecision:
    identity: str
    recommended_action: str
    effective_action: str
    utility: float
    risk: float
    input_tokens: int
    visible_tokens: int
    reason_codes: tuple[str, ...]
    namespace_uri: str = ""
    item_id: str = ""
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.recommended_action not in ITEM_ACTIONS:
            raise ValueError(f"unsupported recommended item action: {self.recommended_action}")
        if self.effective_action not in ITEM_ACTIONS:
            raise ValueError(f"unsupported effective item action: {self.effective_action}")


class AdaptiveContextPolicy:
    """Deterministic, explainable context decision plane.

    This engine owns policy decisions, not context payloads. Callers provide
    reference-only signals. The engine returns recommended actions and a
    content-addressed receipt; it performs no storage, reset, branch, or model
    mutation itself. Shadow mode therefore cannot silently alter runtime state.
    """

    def __init__(self, config: AdaptivePolicyConfig):
        self.config = config

    @staticmethod
    def _utility(signal: ContextPolicySignal) -> float:
        exact_bonus = 1.0 if signal.exact_required else 0.0
        recoverability = 1.0 if signal.recoverable else 0.0
        value = (
            0.42 * float(signal.relevance)
            + 0.24 * float(signal.trust)
            + 0.14 * float(signal.freshness)
            + 0.12 * exact_bonus
            + 0.08 * recoverability
        )
        return round(max(0.0, min(1.0, value)), 6)

    @staticmethod
    def _risk(signal: ContextPolicySignal) -> float:
        if signal.security_denied:
            return 1.0
        value = 0.0
        if signal.tainted:
            value += 0.75
        value += (1.0 - float(signal.trust)) * 0.18
        value += (1.0 - float(signal.freshness)) * 0.07
        return round(max(0.0, min(1.0, value)), 6)

    def _initial_action(
        self,
        signal: ContextPolicySignal,
        *,
        utility: float,
        risk: float,
    ) -> tuple[str, tuple[str, ...]]:
        reasons: list[str] = []
        if signal.security_denied:
            return "ABSTAIN", ("SECURITY_DENY",)
        if signal.tainted and signal.exact_required:
            return "ABSTAIN", ("TAINTED_EXACT_REQUIRED",)
        if signal.exact_required:
            return "KEEP", ("EXACT_REQUIRED",)
        if risk >= 0.75:
            if signal.externalizable and signal.recoverable:
                return "EXTERNALIZE", ("HIGH_RISK_REFERENCE_ONLY",)
            return "ABSTAIN", ("HIGH_RISK_NOT_EXTERNALIZABLE",)
        if utility >= self.config.keep_threshold:
            reasons.append("HIGH_UTILITY")
            return "KEEP", tuple(reasons)
        if utility >= self.config.summarize_threshold and signal.summarizable:
            reasons.append("MEDIUM_UTILITY")
            return "SUMMARIZE", tuple(reasons)
        if utility >= self.config.compress_threshold and signal.compressible:
            reasons.append("LOWER_UTILITY_COMPRESSIBLE")
            return "COMPRESS", tuple(reasons)
        if signal.externalizable and signal.recoverable:
            reasons.append("LOW_UTILITY_RECOVERABLE")
            return "EXTERNALIZE", tuple(reasons)
        if signal.compressible:
            reasons.append("LOW_UTILITY_COMPRESSIBLE")
            return "COMPRESS", tuple(reasons)
        if signal.summarizable:
            reasons.append("LOW_UTILITY_SUMMARIZABLE")
            return "SUMMARIZE", tuple(reasons)
        return "KEEP", ("NO_SAFE_TRANSFORM",)

    def _visible_tokens(self, signal: ContextPolicySignal, action: str) -> int:
        tokens = int(signal.token_count)
        if action == "KEEP":
            return tokens
        if action == "SUMMARIZE":
            return min(tokens, max(24, int(math.ceil(tokens * 0.34))))
        if action == "COMPRESS":
            return min(tokens, max(16, int(math.ceil(tokens * 0.18))))
        if action == "EXTERNALIZE":
            return min(tokens, self.config.external_reference_tokens)
        if action == "ABSTAIN":
            return 0
        raise ValueError(f"unsupported item action: {action}")

    @staticmethod
    def _next_more_economic_action(
        signal: ContextPolicySignal,
        action: str,
    ) -> str | None:
        if signal.exact_required or signal.security_denied:
            return None
        order: list[str] = []
        if signal.summarizable:
            order.append("SUMMARIZE")
        if signal.compressible:
            order.append("COMPRESS")
        if signal.externalizable and signal.recoverable:
            order.append("EXTERNALIZE")
        if not order:
            return None
        current_rank = {"KEEP": -1, "SUMMARIZE": 0, "COMPRESS": 1, "EXTERNALIZE": 2}
        rank = current_rank.get(action, 2)
        for candidate in order:
            if current_rank[candidate] > rank:
                return candidate
        return None

    def _fit_budget(
        self,
        signals: Sequence[ContextPolicySignal],
        decisions: list[dict[str, Any]],
        *,
        current_context_tokens: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        budget = self.config.context_budget_tokens
        target = max(1, int(math.floor(budget * self.config.target_utilization)))
        projected = current_context_tokens + sum(int(row["visible_tokens"]) for row in decisions)
        if projected <= target:
            return decisions, True

        by_identity = {signal.identity: signal for signal in signals}
        candidates = sorted(
            (
                row
                for row in decisions
                if row["recommended_action"] not in {"ABSTAIN", "EXTERNALIZE"}
                and not by_identity[row["identity"]].exact_required
            ),
            key=lambda row: (
                float(row["utility"]),
                -int(row["input_tokens"]),
                row["identity"],
            ),
        )
        for row in candidates:
            signal = by_identity[row["identity"]]
            while projected > target:
                next_action = self._next_more_economic_action(
                    signal,
                    str(row["recommended_action"]),
                )
                if next_action is None:
                    break
                previous = int(row["visible_tokens"])
                row["recommended_action"] = next_action
                row["visible_tokens"] = self._visible_tokens(signal, next_action)
                row["reason_codes"] = tuple(
                    dict.fromkeys((*row["reason_codes"], "BUDGET_PRESSURE"))
                )
                projected -= max(0, previous - int(row["visible_tokens"]))
            if projected <= target:
                break

        return decisions, projected <= budget

    def _session_action(
        self,
        signals: Sequence[ContextPolicySignal],
        decisions: Sequence[Mapping[str, Any]],
        state: ContextPolicyState,
    ) -> tuple[str, tuple[str, ...]]:
        if any(signal.security_denied for signal in signals):
            return "ABSTAIN", ("SECURITY_DENY_PRESENT",)
        if state.irreversible_action_pending and (
            state.unresolved_critical_evidence > 0
            or any(signal.tainted for signal in signals)
        ):
            return "ABSTAIN", ("IRREVERSIBLE_ACTION_WITH_UNRESOLVED_RISK",)
        if any(row["recommended_action"] == "ABSTAIN" for row in decisions):
            return "ABSTAIN", ("ITEM_POLICY_ABSTAIN",)
        if state.task_drift >= self.config.branch_threshold:
            if state.branch_allowed:
                return "BRANCH", ("TASK_DRIFT",)
            return "ABSTAIN", ("TASK_DRIFT_BRANCH_FORBIDDEN",)

        visible = state.current_context_tokens + sum(
            int(row["visible_tokens"]) for row in decisions
        )
        pressure = visible / max(1, self.config.context_budget_tokens)
        reset_safe = all(
            signal.recoverable or signal.token_count == 0
            for signal in signals
            if not signal.security_denied
        )
        if pressure >= self.config.reset_threshold:
            if state.reset_allowed and reset_safe:
                return "RESET", ("CONTEXT_PRESSURE_RECOVERABLE",)
            if not reset_safe:
                return "ABSTAIN", ("RESET_WOULD_LOSE_UNRECOVERABLE_CONTEXT",)
        return "KEEP", ("CONTEXT_CONTINUITY_SAFE",)

    def evaluate(
        self,
        task: str,
        signals: Iterable[ContextPolicySignal],
        *,
        state: ContextPolicyState | None = None,
    ) -> dict[str, Any]:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("adaptive context policy requires task text")
        state = state or ContextPolicyState()
        ordered = tuple(sorted(tuple(signals), key=lambda value: value.identity))
        if len({signal.identity for signal in ordered}) != len(ordered):
            raise ValueError("context policy signal identities must be unique")

        exact_tokens = sum(
            signal.token_count
            for signal in ordered
            if signal.exact_required and not signal.security_denied
        )
        impossible_exact_budget = (
            state.current_context_tokens + exact_tokens
            > self.config.context_budget_tokens
        )

        decisions: list[dict[str, Any]] = []
        for signal in ordered:
            utility = self._utility(signal)
            risk = self._risk(signal)
            action, reasons = self._initial_action(signal, utility=utility, risk=risk)
            decisions.append(
                {
                    "identity": signal.identity,
                    "recommended_action": action,
                    "effective_action": action,
                    "utility": utility,
                    "risk": risk,
                    "input_tokens": signal.token_count,
                    "visible_tokens": self._visible_tokens(signal, action),
                    "reason_codes": reasons,
                    "namespace_uri": signal.namespace_uri,
                    "item_id": signal.item_id,
                    "source_refs": signal.source_refs,
                }
            )

        decisions, budget_fit = self._fit_budget(
            ordered,
            decisions,
            current_context_tokens=state.current_context_tokens,
        )
        if impossible_exact_budget:
            budget_fit = False

        session_action, session_reasons = self._session_action(
            ordered,
            decisions,
            state,
        )
        if not budget_fit and session_action != "ABSTAIN":
            session_action = "ABSTAIN"
            session_reasons = ("BUDGET_CANNOT_BE_SAFELY_SATISFIED",)

        if state.shadow_mode:
            for row in decisions:
                row["effective_action"] = "KEEP"
            effective_session_action = "KEEP"
        else:
            effective_session_action = session_action

        policy_decisions = [ContextPolicyDecision(**row) for row in decisions]
        input_tokens = sum(row.input_tokens for row in policy_decisions)
        recommended_visible = sum(row.visible_tokens for row in policy_decisions)
        effective_visible = (
            input_tokens
            if state.shadow_mode
            else recommended_visible
        )
        receipt_basis = {
            "schema_version": 1,
            "task": normalized_task,
            "config": asdict(self.config),
            "state": asdict(state),
            "signals": [
                {
                    "identity": signal.identity,
                    "token_count": signal.token_count,
                    "relevance": signal.relevance,
                    "trust": signal.trust,
                    "freshness": signal.freshness,
                    "recoverable": signal.recoverable,
                    "exact_required": signal.exact_required,
                    "tainted": signal.tainted,
                    "security_denied": signal.security_denied,
                    "summarizable": signal.summarizable,
                    "compressible": signal.compressible,
                    "externalizable": signal.externalizable,
                    "namespace_uri": signal.namespace_uri,
                    "item_id": signal.item_id,
                    "source_refs": list(signal.source_refs),
                    "metadata": dict(signal.metadata),
                }
                for signal in ordered
            ],
            "recommended_session_action": session_action,
            "effective_session_action": effective_session_action,
            "session_reason_codes": list(session_reasons),
            "decisions": [asdict(row) for row in policy_decisions],
            "budget_fit": budget_fit,
            "shadow_mode": state.shadow_mode,
        }
        return {
            "schema_version": 1,
            "task": normalized_task,
            "recommended_session_action": session_action,
            "effective_session_action": effective_session_action,
            "session_reason_codes": list(session_reasons),
            "decisions": [asdict(row) for row in policy_decisions],
            "metrics": {
                "context_budget_tokens": self.config.context_budget_tokens,
                "current_context_tokens": state.current_context_tokens,
                "input_tokens": input_tokens,
                "recommended_visible_tokens": recommended_visible,
                "effective_visible_tokens": effective_visible,
                "recommended_total_tokens": state.current_context_tokens + recommended_visible,
                "effective_total_tokens": state.current_context_tokens + effective_visible,
                "budget_fit": budget_fit,
                "shadow_mode": state.shadow_mode,
            },
            "receipt": {
                **receipt_basis,
                "receipt_hash": sha256_bytes(canonical_json(receipt_basis)),
            },
        }

    @staticmethod
    def signals_from_context_pack(pack: TaskContextPack) -> tuple[ContextPolicySignal, ...]:
        tier_relevance = {"mandatory": 1.0, "likely": 0.82, "optional": 0.54}
        signals: list[ContextPolicySignal] = []
        for item in pack.items:
            identity_basis = {
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "file_hash": item.file_hash,
            }
            identity = "context-pack:" + sha256_bytes(canonical_json(identity_basis))
            signals.append(
                ContextPolicySignal(
                    identity=identity,
                    token_count=item.tokens,
                    relevance=tier_relevance.get(item.tier, 0.5),
                    trust=0.96,
                    freshness=1.0,
                    recoverable=bool(item.path and item.file_hash),
                    exact_required=item.tier == "mandatory",
                    namespace_uri="",
                    source_refs=(
                        f"repository:{item.path}:{item.start_line}-{item.end_line}",
                        f"file-hash:{item.file_hash}",
                    ),
                    metadata={
                        "tier": item.tier,
                        "kind": item.kind,
                        "path": item.path,
                        "start_line": item.start_line,
                        "end_line": item.end_line,
                        "token_confidence": item.token_confidence,
                    },
                )
            )
        return tuple(signals)

    @staticmethod
    def signals_from_multi_graph(result: Mapping[str, Any]) -> tuple[ContextPolicySignal, ...]:
        rows = list(result.get("candidates") or ())
        if not rows:
            return ()
        max_score = max(float(row.get("score") or 0.0) for row in rows) or 1.0
        signals: list[ContextPolicySignal] = []
        for row in rows:
            identity = str(row.get("identity") or "").strip()
            if not identity:
                raise ValueError("Multi-Graph candidate missing identity")
            trust_levels = set(str(value) for value in row.get("trust_levels") or ())
            trust = 1.0 if "verified" in trust_levels else 0.94 if "trusted" in trust_levels else 0.72
            signals.append(
                ContextPolicySignal(
                    identity=identity,
                    token_count=max(0, int(row.get("estimated_tokens") or 0)),
                    relevance=max(0.0, min(1.0, float(row.get("score") or 0.0) / max_score)),
                    trust=trust,
                    freshness=1.0,
                    recoverable=bool(row.get("namespace_uri") or row.get("item_id")),
                    exact_required=False,
                    namespace_uri=str(row.get("namespace_uri") or ""),
                    item_id=str(row.get("item_id") or ""),
                    source_refs=tuple(str(value) for value in row.get("evidence_refs") or ()),
                    metadata={
                        "graph_kinds": list(row.get("graph_kinds") or ()),
                        "layers": list(row.get("layers") or ()),
                    },
                )
            )
        return tuple(signals)

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "item_actions": sorted(ITEM_ACTIONS),
            "session_actions": sorted(SESSION_ACTIONS),
            "config": asdict(self.config),
            "deterministic": True,
            "explainable": True,
            "shadow_mode_supported": True,
            "payload_authority": False,
            "persistent_store": False,
            "side_effects": False,
        }


__all__ = [
    "ITEM_ACTIONS",
    "SESSION_ACTIONS",
    "ContextPolicySignal",
    "ContextPolicyState",
    "AdaptivePolicyConfig",
    "ContextPolicyDecision",
    "AdaptiveContextPolicy",
]
