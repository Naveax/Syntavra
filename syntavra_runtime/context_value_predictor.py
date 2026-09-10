from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .reacquisition_tax import ReacquisitionTaxGovernor
from .util import canonical_json, sha256_bytes


_ECONOMIC_PRUNE_ACTIONS = frozenset({"SUMMARIZE", "COMPRESS", "EXTERNALIZE"})
_SHA256_ZERO = "0" * 64


def _non_negative(value: float, *, name: str) -> float:
    number = float(value)
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _bounded_probability(value: float, *, name: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be within [0,1]")
    return number


def _optional_sha256(value: str, *, name: str) -> str:
    normalized = str(value or "").casefold()
    if not normalized:
        return ""
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase sha256 when present")
    return normalized


def _seal(basis: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(basis)
    return {**body, "receipt_hash": sha256_bytes(canonical_json(body))}


@dataclass(frozen=True)
class FutureReuseObservation:
    identity: str
    feature_key: str
    horizon_complete: bool
    future_used: bool | None
    observed_reacquisition_cost_units: float
    fallback_reacquisition_cost_units: float
    drop_decision_hash: str
    source_accounting_receipt_hash: str

    def __post_init__(self) -> None:
        identity = str(self.identity).strip()
        feature = str(self.feature_key).strip()
        if not identity or not feature:
            raise ValueError("future reuse observation requires identity and feature_key")
        if self.horizon_complete and self.future_used is None:
            raise ValueError("complete horizon requires a binary future-use label")
        if not self.horizon_complete and self.future_used is not None:
            raise ValueError("incomplete horizon must remain unlabeled")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "feature_key", feature)
        object.__setattr__(
            self,
            "observed_reacquisition_cost_units",
            _non_negative(self.observed_reacquisition_cost_units, name="observed_reacquisition_cost_units"),
        )
        object.__setattr__(
            self,
            "fallback_reacquisition_cost_units",
            _non_negative(self.fallback_reacquisition_cost_units, name="fallback_reacquisition_cost_units"),
        )
        object.__setattr__(
            self,
            "drop_decision_hash",
            _optional_sha256(self.drop_decision_hash, name="drop_decision_hash"),
        )
        object.__setattr__(
            self,
            "source_accounting_receipt_hash",
            _optional_sha256(
                self.source_accounting_receipt_hash,
                name="source_accounting_receipt_hash",
            ),
        )


@dataclass(frozen=True)
class FutureReuseEstimate:
    feature_key: str
    future_use_probability: float
    expected_reacquisition_cost_units: float
    positive_labels: int
    negative_labels: int
    sample_count: int
    source_receipt_hashes: tuple[str, ...]
    estimate_hash: str


@dataclass(frozen=True)
class RetentionCandidate:
    identity: str
    baseline_action: str
    feature_key: str
    carry_cost_units: float
    fallback_reacquisition_cost_units: float
    mandatory: bool = False
    exact_required: bool = False
    mandatory_failure_evidence: bool = False
    mandatory_security_evidence: bool = False
    mandatory_verifier_evidence: bool = False
    invalidated: bool = False

    def __post_init__(self) -> None:
        identity = str(self.identity).strip()
        feature = str(self.feature_key).strip()
        action = str(self.baseline_action).strip().upper()
        if not identity or not feature or not action:
            raise ValueError("retention candidate requires identity, feature_key and baseline_action")
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "feature_key", feature)
        object.__setattr__(self, "baseline_action", action)
        object.__setattr__(
            self,
            "carry_cost_units",
            _non_negative(self.carry_cost_units, name="carry_cost_units"),
        )
        object.__setattr__(
            self,
            "fallback_reacquisition_cost_units",
            _non_negative(
                self.fallback_reacquisition_cost_units,
                name="fallback_reacquisition_cost_units",
            ),
        )


@dataclass(frozen=True)
class RetentionPrediction:
    identity: str
    feature_key: str
    baseline_action: str
    recommended_action: str
    effective_action: str
    future_use_probability: float
    expected_reacquisition_cost_units: float
    carry_cost_units: float
    retention_value: float
    shadow_mode: bool
    hard_pinned: bool
    reason_codes: tuple[str, ...]
    estimate_hash: str
    prediction_hash: str


@dataclass(frozen=True)
class FutureReuseRetentionPolicy:
    shadow_mode: bool = True
    promotion_enabled: bool = False
    beta_prior_alpha: float = 1.0
    beta_prior_beta: float = 1.0
    global_probability_fallback: float = 0.5
    min_complete_samples_for_feature: int = 1
    require_non_inferior_task_success: bool = True
    require_zero_mandatory_evidence_misses: bool = True
    require_non_inferior_reacquisition_cost: bool = True
    require_provider_cost_non_inferior_when_observed: bool = True

    def __post_init__(self) -> None:
        alpha = float(self.beta_prior_alpha)
        beta = float(self.beta_prior_beta)
        if alpha <= 0 or beta <= 0:
            raise ValueError("beta priors must be positive")
        _bounded_probability(self.global_probability_fallback, name="global_probability_fallback")
        if int(self.min_complete_samples_for_feature) < 1:
            raise ValueError("min_complete_samples_for_feature must be positive")
        object.__setattr__(self, "beta_prior_alpha", alpha)
        object.__setattr__(self, "beta_prior_beta", beta)
        object.__setattr__(
            self,
            "min_complete_samples_for_feature",
            int(self.min_complete_samples_for_feature),
        )


@dataclass(frozen=True)
class RetentionEvaluation:
    baseline_task_success_rate: float
    candidate_task_success_rate: float
    mandatory_evidence_misses: int
    baseline_reacquisition_cost_units: float
    candidate_reacquisition_cost_units: float
    baseline_provider_cost_per_success: float | None = None
    candidate_provider_cost_per_success: float | None = None

    def __post_init__(self) -> None:
        _bounded_probability(self.baseline_task_success_rate, name="baseline_task_success_rate")
        _bounded_probability(self.candidate_task_success_rate, name="candidate_task_success_rate")
        if int(self.mandatory_evidence_misses) < 0:
            raise ValueError("mandatory_evidence_misses must be non-negative")
        object.__setattr__(self, "mandatory_evidence_misses", int(self.mandatory_evidence_misses))
        object.__setattr__(
            self,
            "baseline_reacquisition_cost_units",
            _non_negative(
                self.baseline_reacquisition_cost_units,
                name="baseline_reacquisition_cost_units",
            ),
        )
        object.__setattr__(
            self,
            "candidate_reacquisition_cost_units",
            _non_negative(
                self.candidate_reacquisition_cost_units,
                name="candidate_reacquisition_cost_units",
            ),
        )
        pair = (
            self.baseline_provider_cost_per_success,
            self.candidate_provider_cost_per_success,
        )
        if (pair[0] is None) != (pair[1] is None):
            raise ValueError("provider cost-per-success values must be supplied as a pair")
        if pair[0] is not None:
            object.__setattr__(
                self,
                "baseline_provider_cost_per_success",
                _non_negative(pair[0], name="baseline_provider_cost_per_success"),
            )
            object.__setattr__(
                self,
                "candidate_provider_cost_per_success",
                _non_negative(pair[1], name="candidate_provider_cost_per_success"),
            )


class ContextValuePredictor:
    """Deterministic shadow-first future-reuse predictor over frozen receipts.

    This layer owns no raw context or semantic-state persistence. Callers provide
    a stable reference-only ``feature_key`` derived from their existing state
    authority. Only complete frozen horizons produce binary labels.
    """

    schema_version = 1

    def __init__(self, policy: FutureReuseRetentionPolicy | None = None) -> None:
        self.policy = policy or FutureReuseRetentionPolicy()
        self._observations: list[FutureReuseObservation] = []
        self._by_feature: dict[str, tuple[FutureReuseObservation, ...]] = {}

    @staticmethod
    def observations_from_accounting(
        receipt: Mapping[str, Any],
        *,
        horizon_complete: bool,
        feature_by_identity: Mapping[str, str] | None = None,
    ) -> tuple[FutureReuseObservation, ...]:
        ReacquisitionTaxGovernor.verify_receipt(receipt)
        receipt_hash = str(receipt.get("receipt_hash") or "")
        features = dict(feature_by_identity or {})
        raw_decisions = receipt.get("decisions")
        raw_events = receipt.get("events")
        if not isinstance(raw_decisions, list) or not isinstance(raw_events, list):
            raise ValueError("reacquisition accounting receipt requires decisions and events")

        events_by_drop: dict[str, list[Mapping[str, Any]]] = {}
        for raw in raw_events:
            if not isinstance(raw, Mapping) or not bool(raw.get("attributed")):
                continue
            drop_hash = str(raw.get("drop_decision_hash") or "")
            if drop_hash:
                events_by_drop.setdefault(drop_hash, []).append(raw)

        rows: list[FutureReuseObservation] = []
        for raw in raw_decisions:
            if not isinstance(raw, Mapping) or str(raw.get("outcome") or "") != "PRUNE":
                continue
            identity = str(raw.get("identity") or "").strip()
            drop_hash = str(raw.get("decision_hash") or "")
            linked = events_by_drop.get(drop_hash, ())
            observed = sum(
                max(0, int(event.get("retrieval_tokens") or 0))
                + max(0, int(event.get("tool_tokens") or 0))
                + max(0, int(event.get("provider_tokens") or 0))
                for event in linked
            )
            fallback = max(0.0, float(raw.get("expected_reacquisition_cost_units") or 0.0))
            rows.append(
                FutureReuseObservation(
                    identity=identity,
                    feature_key=str(features.get(identity) or "global"),
                    horizon_complete=bool(horizon_complete),
                    future_used=(bool(linked) if horizon_complete else None),
                    observed_reacquisition_cost_units=float(observed),
                    fallback_reacquisition_cost_units=fallback,
                    drop_decision_hash=drop_hash,
                    source_accounting_receipt_hash=receipt_hash,
                )
            )
        return tuple(rows)

    def fit(self, observations: Sequence[FutureReuseObservation]) -> dict[str, Any]:
        ordered = tuple(
            sorted(
                observations,
                key=lambda row: (
                    row.feature_key,
                    row.identity,
                    row.drop_decision_hash,
                    row.source_accounting_receipt_hash,
                ),
            )
        )
        complete = tuple(row for row in ordered if row.horizon_complete and row.future_used is not None)
        grouped: dict[str, list[FutureReuseObservation]] = {}
        for row in complete:
            grouped.setdefault(row.feature_key, []).append(row)
        self._observations = list(ordered)
        self._by_feature = {
            feature: tuple(rows)
            for feature, rows in sorted(grouped.items())
        }
        basis = {
            "schema_version": self.schema_version,
            "policy": asdict(self.policy),
            "observation_count": len(ordered),
            "complete_label_count": len(complete),
            "excluded_incomplete_count": len(ordered) - len(complete),
            "feature_counts": {
                key: len(value) for key, value in self._by_feature.items()
            },
            "source_receipt_hashes": sorted(
                {
                    row.source_accounting_receipt_hash
                    for row in ordered
                    if row.source_accounting_receipt_hash
                }
            ),
            "provider_calls": 0,
            "provider_tokens": 0,
            "provider_savings_claim": False,
        }
        return _seal(basis)

    def _rows_for_feature(self, feature_key: str) -> tuple[FutureReuseObservation, ...]:
        rows = self._by_feature.get(feature_key, ())
        if len(rows) >= self.policy.min_complete_samples_for_feature:
            return rows
        return tuple(
            row
            for values in self._by_feature.values()
            for row in values
        )

    def estimate(
        self,
        feature_key: str,
        *,
        fallback_reacquisition_cost_units: float,
    ) -> FutureReuseEstimate:
        feature = str(feature_key).strip()
        if not feature:
            raise ValueError("feature_key is required")
        fallback = _non_negative(
            fallback_reacquisition_cost_units,
            name="fallback_reacquisition_cost_units",
        )
        rows = self._rows_for_feature(feature)
        positives = sum(row.future_used is True for row in rows)
        negatives = sum(row.future_used is False for row in rows)
        count = positives + negatives
        if count:
            probability = (
                positives + self.policy.beta_prior_alpha
            ) / (
                count + self.policy.beta_prior_alpha + self.policy.beta_prior_beta
            )
        else:
            probability = self.policy.global_probability_fallback

        observed_positive_costs = [
            (
                row.observed_reacquisition_cost_units
                if row.observed_reacquisition_cost_units > 0
                else row.fallback_reacquisition_cost_units
            )
            for row in rows
            if row.future_used is True
        ]
        expected_reacquisition = (
            sum(observed_positive_costs) / len(observed_positive_costs)
            if observed_positive_costs
            else fallback
        )
        source_hashes = tuple(
            sorted(
                {
                    row.source_accounting_receipt_hash
                    for row in rows
                    if row.source_accounting_receipt_hash
                }
            )
        )
        basis = {
            "schema_version": self.schema_version,
            "feature_key": feature,
            "future_use_probability": round(probability, 9),
            "expected_reacquisition_cost_units": round(expected_reacquisition, 6),
            "positive_labels": positives,
            "negative_labels": negatives,
            "sample_count": count,
            "source_receipt_hashes": list(source_hashes),
        }
        estimate_hash = sha256_bytes(canonical_json(basis))
        return FutureReuseEstimate(
            feature_key=feature,
            future_use_probability=basis["future_use_probability"],
            expected_reacquisition_cost_units=basis["expected_reacquisition_cost_units"],
            positive_labels=positives,
            negative_labels=negatives,
            sample_count=count,
            source_receipt_hashes=source_hashes,
            estimate_hash=estimate_hash,
        )

    @staticmethod
    def _hard_pinned(candidate: RetentionCandidate) -> bool:
        return any(
            (
                candidate.mandatory,
                candidate.exact_required,
                candidate.mandatory_failure_evidence,
                candidate.mandatory_security_evidence,
                candidate.mandatory_verifier_evidence,
            )
        )

    def predict(self, candidate: RetentionCandidate) -> RetentionPrediction:
        if candidate.invalidated:
            basis = {
                "schema_version": self.schema_version,
                "identity": candidate.identity,
                "feature_key": candidate.feature_key,
                "baseline_action": candidate.baseline_action,
                "recommended_action": candidate.baseline_action,
                "effective_action": candidate.baseline_action,
                "future_use_probability": 0.0,
                "expected_reacquisition_cost_units": 0.0,
                "carry_cost_units": candidate.carry_cost_units,
                "retention_value": 0.0,
                "shadow_mode": self.policy.shadow_mode,
                "hard_pinned": False,
                "reason_codes": ["INVALIDATION_OUTSIDE_LEARNED_RETENTION"],
                "estimate_hash": _SHA256_ZERO,
            }
        elif self._hard_pinned(candidate):
            basis = {
                "schema_version": self.schema_version,
                "identity": candidate.identity,
                "feature_key": candidate.feature_key,
                "baseline_action": candidate.baseline_action,
                "recommended_action": "KEEP",
                "effective_action": "KEEP",
                "future_use_probability": 1.0,
                "expected_reacquisition_cost_units": candidate.fallback_reacquisition_cost_units,
                "carry_cost_units": candidate.carry_cost_units,
                "retention_value": max(
                    0.0,
                    candidate.fallback_reacquisition_cost_units - candidate.carry_cost_units,
                ),
                "shadow_mode": self.policy.shadow_mode,
                "hard_pinned": True,
                "reason_codes": ["MANDATORY_EVIDENCE_PINNED_OUTSIDE_PREDICTOR"],
                "estimate_hash": _SHA256_ZERO,
            }
        else:
            estimate = self.estimate(
                candidate.feature_key,
                fallback_reacquisition_cost_units=candidate.fallback_reacquisition_cost_units,
            )
            retention_value = (
                estimate.future_use_probability
                * estimate.expected_reacquisition_cost_units
                - candidate.carry_cost_units
            )
            if candidate.baseline_action not in _ECONOMIC_PRUNE_ACTIONS:
                recommended = candidate.baseline_action
                reasons = ["NO_ECONOMIC_PRUNE_BASELINE"]
            elif retention_value > 0:
                recommended = "KEEP"
                reasons = ["POSITIVE_FUTURE_REUSE_RETENTION_VALUE"]
            else:
                recommended = candidate.baseline_action
                reasons = ["NON_POSITIVE_FUTURE_REUSE_RETENTION_VALUE"]
            effective = (
                candidate.baseline_action
                if self.policy.shadow_mode or not self.policy.promotion_enabled
                else recommended
            )
            basis = {
                "schema_version": self.schema_version,
                "identity": candidate.identity,
                "feature_key": candidate.feature_key,
                "baseline_action": candidate.baseline_action,
                "recommended_action": recommended,
                "effective_action": effective,
                "future_use_probability": estimate.future_use_probability,
                "expected_reacquisition_cost_units": estimate.expected_reacquisition_cost_units,
                "carry_cost_units": candidate.carry_cost_units,
                "retention_value": round(retention_value, 6),
                "shadow_mode": self.policy.shadow_mode,
                "hard_pinned": False,
                "reason_codes": reasons,
                "estimate_hash": estimate.estimate_hash,
            }
        prediction_hash = sha256_bytes(canonical_json(basis))
        return RetentionPrediction(
            identity=basis["identity"],
            feature_key=basis["feature_key"],
            baseline_action=basis["baseline_action"],
            recommended_action=basis["recommended_action"],
            effective_action=basis["effective_action"],
            future_use_probability=basis["future_use_probability"],
            expected_reacquisition_cost_units=basis["expected_reacquisition_cost_units"],
            carry_cost_units=basis["carry_cost_units"],
            retention_value=basis["retention_value"],
            shadow_mode=basis["shadow_mode"],
            hard_pinned=basis["hard_pinned"],
            reason_codes=tuple(basis["reason_codes"]),
            estimate_hash=basis["estimate_hash"],
            prediction_hash=prediction_hash,
        )

    def evaluate_promotion(self, evaluation: RetentionEvaluation) -> dict[str, Any]:
        reasons: list[str] = []
        if (
            self.policy.require_zero_mandatory_evidence_misses
            and evaluation.mandatory_evidence_misses != 0
        ):
            reasons.append("MANDATORY_EVIDENCE_MISS")
        if (
            self.policy.require_non_inferior_task_success
            and evaluation.candidate_task_success_rate < evaluation.baseline_task_success_rate
        ):
            reasons.append("TASK_SUCCESS_REGRESSION")
        if (
            self.policy.require_non_inferior_reacquisition_cost
            and evaluation.candidate_reacquisition_cost_units
            > evaluation.baseline_reacquisition_cost_units
        ):
            reasons.append("REACQUISITION_COST_REGRESSION")
        provider_pair = (
            evaluation.baseline_provider_cost_per_success is not None
            and evaluation.candidate_provider_cost_per_success is not None
        )
        if (
            self.policy.require_provider_cost_non_inferior_when_observed
            and provider_pair
            and evaluation.candidate_provider_cost_per_success
            > evaluation.baseline_provider_cost_per_success
        ):
            reasons.append("PROVIDER_COST_PER_SUCCESS_REGRESSION")
        evidence_gate_ok = not reasons
        promotion_allowed = (
            self.policy.promotion_enabled
            and not self.policy.shadow_mode
            and evidence_gate_ok
        )
        return _seal(
            {
                "schema_version": self.schema_version,
                "family": "future-reuse-retention-promotion-gate",
                "mechanism_id": "U5-P0-03",
                "evaluation": asdict(evaluation),
                "evidence_gate_ok": evidence_gate_ok,
                "promotion_allowed": promotion_allowed,
                "reason_codes": reasons,
                "provider_evidence_present": provider_pair,
                "provider_savings_claim": False,
            }
        )

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.schema_version,
            "mechanism_id": "U5-P0-03",
            "shadow_first": True,
            "raw_context_authority": False,
            "semantic_state_authority": False,
            "complete_horizon_labels_only": True,
            "mandatory_evidence_outside_learned_decisions": True,
            "provider_calls": 0,
            "provider_tokens": 0,
            "provider_savings_claim": False,
        }

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> bool:
        body = dict(receipt)
        observed = _optional_sha256(str(body.pop("receipt_hash", "")), name="receipt_hash")
        if not observed:
            raise ValueError("receipt hash is required")
        if observed != sha256_bytes(canonical_json(body)):
            raise ValueError("future-reuse receipt hash mismatch")
        return True


__all__ = [
    "ContextValuePredictor",
    "FutureReuseEstimate",
    "FutureReuseObservation",
    "FutureReuseRetentionPolicy",
    "RetentionCandidate",
    "RetentionEvaluation",
    "RetentionPrediction",
]
