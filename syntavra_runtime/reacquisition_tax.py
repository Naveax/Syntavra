from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .util import canonical_json, sha256_bytes


_REACQUISITION_EVENT_KINDS = frozenset(
    {"read", "search", "retrieve", "tool", "provider", "reconstruct"}
)
_ECONOMIC_PRUNE_ACTIONS = frozenset({"SUMMARIZE", "COMPRESS", "EXTERNALIZE"})
_SHA256_ZERO = "0" * 64


def _non_negative_int(value: int, *, name: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _non_negative_float(value: float, *, name: str) -> float:
    number = float(value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _optional_sha256(value: str, *, name: str) -> str:
    normalized = str(value or "").casefold()
    if not normalized:
        return ""
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase sha256 when present")
    return normalized


def _sealed_receipt(basis: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(basis)
    return {**body, "receipt_hash": sha256_bytes(canonical_json(body))}


@dataclass(frozen=True)
class ReacquisitionTaxPolicy:
    enabled: bool = True
    fail_closed_missing_estimate: bool = True
    latency_token_equivalent_per_ms: float = 0.0
    microusd_token_equivalent: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "latency_token_equivalent_per_ms",
            _non_negative_float(
                self.latency_token_equivalent_per_ms,
                name="latency_token_equivalent_per_ms",
            ),
        )
        object.__setattr__(
            self,
            "microusd_token_equivalent",
            _non_negative_float(self.microusd_token_equivalent, name="microusd_token_equivalent"),
        )


@dataclass(frozen=True)
class ContextCostEstimate:
    identity: str
    expected_carry_tokens: int
    expected_retrieval_tokens: int = 0
    expected_tool_tokens: int = 0
    expected_provider_tokens: int = 0
    expected_latency_ms: float = 0.0
    expected_carry_cost_microusd: float = 0.0
    expected_reacquisition_cost_microusd: float = 0.0
    tokenizer_method: str = "UNSPECIFIED"
    exact_target_tokenizer: bool = False
    mandatory: bool = False
    exact_required: bool = False
    invalidated: bool = False
    security_forced_drop: bool = False
    baseline_usage_receipt_hash: str = ""
    baseline_token_receipt_hash: str = ""

    def __post_init__(self) -> None:
        identity = str(self.identity).strip()
        if not identity:
            raise ValueError("context cost estimate requires identity")
        object.__setattr__(self, "identity", identity)
        for name in (
            "expected_carry_tokens",
            "expected_retrieval_tokens",
            "expected_tool_tokens",
            "expected_provider_tokens",
        ):
            object.__setattr__(self, name, _non_negative_int(getattr(self, name), name=name))
        object.__setattr__(
            self,
            "expected_latency_ms",
            _non_negative_float(self.expected_latency_ms, name="expected_latency_ms"),
        )
        object.__setattr__(
            self,
            "expected_carry_cost_microusd",
            _non_negative_float(
                self.expected_carry_cost_microusd,
                name="expected_carry_cost_microusd",
            ),
        )
        object.__setattr__(
            self,
            "expected_reacquisition_cost_microusd",
            _non_negative_float(
                self.expected_reacquisition_cost_microusd,
                name="expected_reacquisition_cost_microusd",
            ),
        )
        usage_hash = _optional_sha256(
            self.baseline_usage_receipt_hash,
            name="baseline_usage_receipt_hash",
        )
        token_hash = _optional_sha256(
            self.baseline_token_receipt_hash,
            name="baseline_token_receipt_hash",
        )
        if bool(usage_hash) != bool(token_hash):
            raise ValueError("baseline provider receipt hashes must be supplied as a pair")
        object.__setattr__(self, "baseline_usage_receipt_hash", usage_hash)
        object.__setattr__(self, "baseline_token_receipt_hash", token_hash)

    @property
    def expected_reacquisition_tokens(self) -> int:
        return (
            self.expected_retrieval_tokens
            + self.expected_tool_tokens
            + self.expected_provider_tokens
        )


@dataclass(frozen=True)
class ReacquisitionTaxDecision:
    identity: str
    original_action: str
    governed_action: str
    outcome: str
    reason_codes: tuple[str, ...]
    expected_carry_tokens: int
    expected_reacquisition_tokens: int
    expected_carry_cost_units: float
    expected_reacquisition_cost_units: float
    tokenizer_method: str
    exact_target_tokenizer: bool
    estimate_hash: str
    policy_hash: str
    baseline_usage_receipt_hash: str
    baseline_token_receipt_hash: str
    decision_hash: str


@dataclass(frozen=True)
class ReacquisitionEvent:
    sequence: int
    identity: str
    event_kind: str
    retrieval_tokens: int
    tool_tokens: int
    provider_tokens: int
    latency_ms: float
    cost_microusd: float
    source_ref: str
    usage_receipt_hash: str
    token_receipt_hash: str
    attributed: bool
    drop_decision_hash: str
    event_hash: str


class ReacquisitionTaxGovernor:
    """Economic veto/accounting layer over existing context policy decisions.

    It owns no context payload persistence. It deterministically vetoes economic
    pruning when expected reacquisition is dearer and attributes later recovery
    work to the latest prior economic prune for the same identity.
    """

    schema_version = 1

    def __init__(self, policy: ReacquisitionTaxPolicy | None = None) -> None:
        self.policy = policy or ReacquisitionTaxPolicy()
        self._decisions: list[ReacquisitionTaxDecision] = []
        self._latest_prune: dict[str, ReacquisitionTaxDecision] = {}
        self._events: list[ReacquisitionEvent] = []

    @staticmethod
    def _estimate_hash(estimate: ContextCostEstimate | None) -> str:
        if estimate is None:
            return _SHA256_ZERO
        return sha256_bytes(canonical_json(asdict(estimate)))

    def _cost_units(self, estimate: ContextCostEstimate) -> tuple[float, float]:
        carry = (
            float(estimate.expected_carry_tokens)
            + estimate.expected_carry_cost_microusd * self.policy.microusd_token_equivalent
        )
        reacquisition = (
            float(estimate.expected_reacquisition_tokens)
            + estimate.expected_latency_ms * self.policy.latency_token_equivalent_per_ms
            + estimate.expected_reacquisition_cost_microusd * self.policy.microusd_token_equivalent
        )
        return round(carry, 6), round(reacquisition, 6)

    def decide(
        self,
        *,
        identity: str,
        original_action: str,
        estimate: ContextCostEstimate | None,
        policy_hash: str,
    ) -> ReacquisitionTaxDecision:
        subject = str(identity).strip()
        if not subject:
            raise ValueError("reacquisition tax decision requires identity")
        action = str(original_action).strip().upper()
        if not action:
            raise ValueError("reacquisition tax decision requires original action")
        normalized_policy_hash = _optional_sha256(policy_hash, name="policy_hash") or _SHA256_ZERO
        if estimate is not None and estimate.identity != subject:
            raise ValueError("reacquisition estimate identity does not match decision identity")

        if estimate is None:
            carry_units = reacquisition_units = 0.0
            carry_tokens = reacquisition_tokens = 0
            tokenizer_method = "MISSING"
            exact_target_tokenizer = False
            baseline_usage = baseline_token = ""
            if action in _ECONOMIC_PRUNE_ACTIONS and self.policy.fail_closed_missing_estimate:
                governed, outcome = "KEEP", "RETAIN"
                reasons = ("REACQUISITION_ESTIMATE_MISSING_FAIL_CLOSED",)
            else:
                governed, outcome = action, "UNCHANGED"
                reasons = ("REACQUISITION_ESTIMATE_MISSING_NO_VETO",)
        else:
            carry_units, reacquisition_units = self._cost_units(estimate)
            carry_tokens = estimate.expected_carry_tokens
            reacquisition_tokens = estimate.expected_reacquisition_tokens
            tokenizer_method = str(estimate.tokenizer_method)
            exact_target_tokenizer = bool(estimate.exact_target_tokenizer)
            baseline_usage = estimate.baseline_usage_receipt_hash
            baseline_token = estimate.baseline_token_receipt_hash
            if estimate.security_forced_drop:
                governed, outcome = "ABSTAIN", "FORCED_DROP"
                reasons = ("SECURITY_FORCED_DROP",)
            elif estimate.invalidated:
                governed, outcome = "ABSTAIN", "FORCED_DROP"
                reasons = ("INVALIDATED_CONTEXT_DROP",)
            elif estimate.mandatory or estimate.exact_required:
                governed, outcome = "KEEP", "RETAIN"
                reasons = ("MANDATORY_OR_EXACT_CONTEXT_PINNED",)
            elif not self.policy.enabled and action in _ECONOMIC_PRUNE_ACTIONS:
                governed, outcome = "KEEP", "RETAIN"
                reasons = ("ROLLBACK_SAFE_RETAIN",)
            elif action not in _ECONOMIC_PRUNE_ACTIONS:
                governed, outcome = action, "UNCHANGED"
                reasons = ("NO_ECONOMIC_PRUNE_REQUESTED",)
            elif reacquisition_units > carry_units:
                governed, outcome = "KEEP", "RETAIN"
                reasons = ("REACQUISITION_COST_EXCEEDS_CARRY",)
            else:
                governed, outcome = action, "PRUNE"
                reasons = ("CARRY_COST_AT_LEAST_REACQUISITION",)

        estimate_hash = self._estimate_hash(estimate)
        basis = {
            "schema_version": self.schema_version,
            "identity": subject,
            "original_action": action,
            "governed_action": governed,
            "outcome": outcome,
            "reason_codes": list(reasons),
            "expected_carry_tokens": carry_tokens,
            "expected_reacquisition_tokens": reacquisition_tokens,
            "expected_carry_cost_units": carry_units,
            "expected_reacquisition_cost_units": reacquisition_units,
            "tokenizer_method": tokenizer_method,
            "exact_target_tokenizer": exact_target_tokenizer,
            "estimate_hash": estimate_hash,
            "policy_hash": normalized_policy_hash,
            "baseline_usage_receipt_hash": baseline_usage,
            "baseline_token_receipt_hash": baseline_token,
        }
        decision_hash = sha256_bytes(canonical_json(basis))
        decision = ReacquisitionTaxDecision(
            identity=subject,
            original_action=action,
            governed_action=governed,
            outcome=outcome,
            reason_codes=reasons,
            expected_carry_tokens=carry_tokens,
            expected_reacquisition_tokens=reacquisition_tokens,
            expected_carry_cost_units=carry_units,
            expected_reacquisition_cost_units=reacquisition_units,
            tokenizer_method=tokenizer_method,
            exact_target_tokenizer=exact_target_tokenizer,
            estimate_hash=estimate_hash,
            policy_hash=normalized_policy_hash,
            baseline_usage_receipt_hash=baseline_usage,
            baseline_token_receipt_hash=baseline_token,
            decision_hash=decision_hash,
        )
        self._decisions.append(decision)
        if outcome == "PRUNE":
            self._latest_prune[subject] = decision
        else:
            self._latest_prune.pop(subject, None)
        return decision

    @staticmethod
    def _visible_tokens_for_action(row: Mapping[str, Any], action: str) -> int:
        if action == "KEEP":
            return max(0, int(row.get("input_tokens") or 0))
        if action == "ABSTAIN":
            return 0
        return max(0, int(row.get("visible_tokens") or 0))

    @staticmethod
    def _verify_policy_receipt(receipt: Mapping[str, Any]) -> str:
        body = dict(receipt)
        observed = _optional_sha256(str(body.pop("receipt_hash", "")), name="policy receipt hash")
        if not observed:
            raise ValueError("adaptive policy receipt hash is required")
        if observed != sha256_bytes(canonical_json(body)):
            raise ValueError("adaptive policy receipt hash mismatch")
        return observed

    def govern_policy_result(
        self,
        result: Mapping[str, Any],
        estimates: Sequence[ContextCostEstimate],
    ) -> dict[str, Any]:
        receipt = result.get("receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("reacquisition tax governor requires adaptive policy receipt")
        original_policy_hash = self._verify_policy_receipt(receipt)
        estimates_tuple = tuple(estimates)
        estimate_by_identity = {item.identity: item for item in estimates_tuple}
        if len(estimate_by_identity) != len(estimates_tuple):
            raise ValueError("reacquisition estimates must have unique identities")

        rows = result.get("decisions")
        if not isinstance(rows, list):
            raise ValueError("adaptive policy result decisions must be a list")
        receipt_rows = receipt.get("decisions")
        if canonical_json(rows) != canonical_json(receipt_rows):
            raise ValueError("adaptive policy result/receipt decision drift")

        governed_rows: list[dict[str, Any]] = []
        tax_decisions: list[ReacquisitionTaxDecision] = []
        shadow_mode = bool(receipt.get("shadow_mode"))
        forced_abstain = False
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("adaptive policy decision row must be an object")
            row = dict(raw)
            identity = str(row.get("identity") or "")
            decision = self.decide(
                identity=identity,
                original_action=str(row.get("recommended_action") or ""),
                estimate=estimate_by_identity.get(identity),
                policy_hash=original_policy_hash,
            )
            tax_decisions.append(decision)
            row["recommended_action"] = decision.governed_action
            if decision.outcome == "FORCED_DROP":
                row["effective_action"] = decision.governed_action
                forced_abstain = True
            elif shadow_mode:
                row["effective_action"] = "KEEP"
            else:
                row["effective_action"] = decision.governed_action
            row["visible_tokens"] = self._visible_tokens_for_action(row, row["recommended_action"])
            row["reason_codes"] = tuple(
                dict.fromkeys(
                    (
                        *(str(value) for value in row.get("reason_codes") or ()),
                        *decision.reason_codes,
                    )
                )
            )
            governed_rows.append(row)

        input_tokens = sum(max(0, int(row.get("input_tokens") or 0)) for row in governed_rows)
        recommended_visible = sum(max(0, int(row.get("visible_tokens") or 0)) for row in governed_rows)
        effective_visible = (
            input_tokens
            if shadow_mode and not forced_abstain
            else sum(
                self._visible_tokens_for_action(row, str(row.get("effective_action") or ""))
                for row in governed_rows
            )
        )
        original_metrics = result.get("metrics")
        metrics = dict(original_metrics) if isinstance(original_metrics, Mapping) else {}
        current_context_tokens = max(0, int(metrics.get("current_context_tokens") or 0))
        context_budget_tokens = max(0, int(metrics.get("context_budget_tokens") or 0))
        metrics.update(
            {
                "input_tokens": input_tokens,
                "recommended_visible_tokens": recommended_visible,
                "effective_visible_tokens": effective_visible,
                "recommended_total_tokens": current_context_tokens + recommended_visible,
                "effective_total_tokens": current_context_tokens + effective_visible,
            }
        )

        recommended_session_action = str(
            result.get("recommended_session_action")
            or receipt.get("recommended_session_action")
            or "KEEP"
        )
        effective_session_action = str(
            result.get("effective_session_action")
            or receipt.get("effective_session_action")
            or "KEEP"
        )
        session_reasons = list(
            result.get("session_reason_codes")
            or receipt.get("session_reason_codes")
            or ()
        )
        if forced_abstain:
            recommended_session_action = effective_session_action = "ABSTAIN"
            session_reasons = list(dict.fromkeys((*session_reasons, "REACQUISITION_FORCED_DROP")))
        elif context_budget_tokens > 0 and current_context_tokens + recommended_visible > context_budget_tokens:
            recommended_session_action = "ABSTAIN"
            if not shadow_mode:
                effective_session_action = "ABSTAIN"
            session_reasons = list(
                dict.fromkeys((*session_reasons, "REACQUISITION_RETENTION_EXCEEDS_CONTEXT_BUDGET"))
            )
            metrics["budget_fit"] = False

        new_receipt_basis = dict(receipt)
        new_receipt_basis.pop("receipt_hash", None)
        new_receipt_basis["recommended_session_action"] = recommended_session_action
        new_receipt_basis["effective_session_action"] = effective_session_action
        new_receipt_basis["session_reason_codes"] = session_reasons
        new_receipt_basis["decisions"] = governed_rows
        new_receipt = {
            **new_receipt_basis,
            "receipt_hash": sha256_bytes(canonical_json(new_receipt_basis)),
        }

        tax_receipt = _sealed_receipt(
            {
                "schema_version": self.schema_version,
                "family": "reacquisition-tax-governor",
                "mechanism_id": "U5-P0-02",
                "source_policy_receipt_hash": original_policy_hash,
                "governed_policy_receipt_hash": new_receipt["receipt_hash"],
                "policy": asdict(self.policy),
                "decisions": [asdict(item) for item in tax_decisions],
                "retained_count": sum(item.outcome == "RETAIN" for item in tax_decisions),
                "pruned_count": sum(item.outcome == "PRUNE" for item in tax_decisions),
                "forced_drop_count": sum(item.outcome == "FORCED_DROP" for item in tax_decisions),
                "provider_savings_claim": False,
            }
        )
        output = dict(result)
        output.update(
            {
                "recommended_session_action": recommended_session_action,
                "effective_session_action": effective_session_action,
                "session_reason_codes": session_reasons,
                "decisions": governed_rows,
                "metrics": metrics,
                "receipt": new_receipt,
                "reacquisition_tax_receipt": tax_receipt,
            }
        )
        return output

    def record_reacquisition(
        self,
        *,
        identity: str,
        event_kind: str,
        retrieval_tokens: int = 0,
        tool_tokens: int = 0,
        provider_tokens: int = 0,
        latency_ms: float = 0.0,
        cost_microusd: float = 0.0,
        source_ref: str = "",
        usage_receipt_hash: str = "",
        token_receipt_hash: str = "",
    ) -> ReacquisitionEvent:
        subject = str(identity).strip()
        if not subject:
            raise ValueError("reacquisition event requires identity")
        kind = str(event_kind).casefold()
        if kind not in _REACQUISITION_EVENT_KINDS:
            raise ValueError(f"unsupported reacquisition event kind: {event_kind}")
        retrieval = _non_negative_int(retrieval_tokens, name="retrieval_tokens")
        tool = _non_negative_int(tool_tokens, name="tool_tokens")
        provider = _non_negative_int(provider_tokens, name="provider_tokens")
        latency = _non_negative_float(latency_ms, name="latency_ms")
        money = _non_negative_float(cost_microusd, name="cost_microusd")
        usage_hash = _optional_sha256(usage_receipt_hash, name="usage_receipt_hash")
        token_hash = _optional_sha256(token_receipt_hash, name="token_receipt_hash")
        if bool(usage_hash) != bool(token_hash):
            raise ValueError("provider reacquisition receipt hashes must be supplied as a pair")

        drop = self._latest_prune.get(subject)
        attributed = drop is not None
        sequence = len(self._events) + 1
        basis = {
            "schema_version": self.schema_version,
            "sequence": sequence,
            "identity": subject,
            "event_kind": kind,
            "retrieval_tokens": retrieval,
            "tool_tokens": tool,
            "provider_tokens": provider,
            "latency_ms": latency,
            "cost_microusd": money,
            "source_ref": str(source_ref),
            "usage_receipt_hash": usage_hash,
            "token_receipt_hash": token_hash,
            "attributed": attributed,
            "drop_decision_hash": drop.decision_hash if drop is not None else "",
        }
        event_hash = sha256_bytes(canonical_json(basis))
        event = ReacquisitionEvent(
            sequence=sequence,
            identity=subject,
            event_kind=kind,
            retrieval_tokens=retrieval,
            tool_tokens=tool,
            provider_tokens=provider,
            latency_ms=latency,
            cost_microusd=money,
            source_ref=str(source_ref),
            usage_receipt_hash=usage_hash,
            token_receipt_hash=token_hash,
            attributed=attributed,
            drop_decision_hash=basis["drop_decision_hash"],
            event_hash=event_hash,
        )
        self._events.append(event)
        return event

    def accounting_receipt(self) -> dict[str, Any]:
        prunes = [item for item in self._decisions if item.outcome == "PRUNE"]
        attributed = [event for event in self._events if event.attributed]
        unattributed = [event for event in self._events if not event.attributed]
        counterfactual_retention_tokens = sum(item.expected_carry_tokens for item in prunes)
        retrieval_tokens = sum(event.retrieval_tokens for event in attributed)
        tool_tokens = sum(event.tool_tokens for event in attributed)
        provider_tokens = sum(event.provider_tokens for event in attributed)
        total_reacquisition_tokens = retrieval_tokens + tool_tokens + provider_tokens
        latency_ms = round(sum(event.latency_ms for event in attributed), 6)
        cost_microusd = round(sum(event.cost_microusd for event in attributed), 6)

        baseline_provider_evidence_complete = bool(prunes) and all(
            bool(item.baseline_usage_receipt_hash and item.baseline_token_receipt_hash)
            for item in prunes
        )
        provider_events = [event for event in attributed if event.provider_tokens > 0]
        reacquisition_provider_evidence_complete = bool(provider_events) and all(
            bool(event.usage_receipt_hash and event.token_receipt_hash)
            for event in provider_events
        )
        provider_verified = baseline_provider_evidence_complete and reacquisition_provider_evidence_complete
        provider_verified_net_token_delta = (
            provider_tokens - counterfactual_retention_tokens if provider_verified else None
        )

        return _sealed_receipt(
            {
                "schema_version": self.schema_version,
                "family": "reacquisition-tax-accounting",
                "mechanism_id": "U5-P0-02",
                "counterfactual_retention_tokens": counterfactual_retention_tokens,
                "reacquisition_tokens": {
                    "retrieval": retrieval_tokens,
                    "tool": tool_tokens,
                    "provider": provider_tokens,
                    "total": total_reacquisition_tokens,
                },
                "reacquisition_latency_ms": latency_ms,
                "reacquisition_cost_microusd": cost_microusd,
                "accounted_net_token_delta": total_reacquisition_tokens - counterfactual_retention_tokens,
                "decision_count": len(self._decisions),
                "prune_count": len(prunes),
                "attributed_event_count": len(attributed),
                "unattributed_event_count": len(unattributed),
                "baseline_provider_evidence_complete": baseline_provider_evidence_complete,
                "reacquisition_provider_evidence_complete": reacquisition_provider_evidence_complete,
                "provider_verified": provider_verified,
                "provider_verified_net_token_delta": provider_verified_net_token_delta,
                "provider_savings_claim": False,
                "decisions": [asdict(item) for item in self._decisions],
                "events": [asdict(item) for item in self._events],
            }
        )

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> bool:
        body = dict(receipt)
        observed = _optional_sha256(str(body.pop("receipt_hash", "")), name="receipt_hash")
        if not observed:
            raise ValueError("receipt hash is required")
        if observed != sha256_bytes(canonical_json(body)):
            raise ValueError("reacquisition tax receipt hash mismatch")
        return True


__all__ = [
    "ContextCostEstimate",
    "ReacquisitionEvent",
    "ReacquisitionTaxDecision",
    "ReacquisitionTaxGovernor",
    "ReacquisitionTaxPolicy",
]
