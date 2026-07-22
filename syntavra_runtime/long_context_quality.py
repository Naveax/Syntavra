from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .release_identity import CHANNEL, VERSION


LONG_CONTEXT_TIERS: tuple[int, ...] = (32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000, 2_000_000, 10_000_000)
TASK_FAMILIES: tuple[str, ...] = (
    "needle-retrieval",
    "temporal-supersession",
    "multi-hop-evidence",
    "repository-history",
    "cross-session-continuity",
    "recursive-map-reduce",
)


@dataclass(frozen=True)
class LongContextReceipt:
    receipt_id: str
    case_id: str
    task_family: str
    tier_tokens: int
    arm: str
    repetition: int
    repository_hash: str
    provider: str
    model: str
    answer_quality: float
    required_fact_recall: float
    stale_fact_rejection: float
    evidence_precision: float
    exact_recovery: bool
    forced_restart: bool
    continuity_restored: bool
    wall_time_ms: float
    input_tokens: int
    output_tokens: int
    synthetic: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LongContextReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            case_id=str(value.get("case_id", "")),
            task_family=str(value.get("task_family", "")),
            tier_tokens=int(value.get("tier_tokens", 0)),
            arm=str(value.get("arm", "")),
            repetition=int(value.get("repetition", 0)),
            repository_hash=str(value.get("repository_hash", "")),
            provider=str(value.get("provider", "")),
            model=str(value.get("model", "")),
            answer_quality=float(value.get("answer_quality", -1)),
            required_fact_recall=float(value.get("required_fact_recall", -1)),
            stale_fact_rejection=float(value.get("stale_fact_rejection", -1)),
            evidence_precision=float(value.get("evidence_precision", -1)),
            exact_recovery=bool(value.get("exact_recovery", False)),
            forced_restart=bool(value.get("forced_restart", True)),
            continuity_restored=bool(value.get("continuity_restored", False)),
            wall_time_ms=float(value.get("wall_time_ms", -1)),
            input_tokens=int(value.get("input_tokens", -1)),
            output_tokens=int(value.get("output_tokens", -1)),
            synthetic=bool(value.get("synthetic", True)),
        )

    def validate(self) -> tuple[str, ...]:
        reasons: list[str] = []
        for key in ("receipt_id", "case_id", "arm", "repository_hash", "provider", "model"):
            if not getattr(self, key):
                reasons.append(f"missing-{key.replace('_', '-')}")
        if self.task_family not in TASK_FAMILIES:
            reasons.append("unsupported-task-family")
        if self.tier_tokens not in LONG_CONTEXT_TIERS:
            reasons.append("unsupported-tier")
        if self.arm not in {"baseline", "syntavra"}:
            reasons.append("unsupported-arm")
        if self.repetition < 1:
            reasons.append("invalid-repetition")
        for key in ("answer_quality", "required_fact_recall", "stale_fact_rejection", "evidence_precision"):
            value = float(getattr(self, key))
            if not 0.0 <= value <= 1.0:
                reasons.append(f"invalid-{key.replace('_', '-')}")
        if self.wall_time_ms < 0:
            reasons.append("invalid-wall-time")
        if self.input_tokens < 0 or self.output_tokens < 0:
            reasons.append("invalid-token-count")
        return tuple(dict.fromkeys(reasons))


class LongContextQualityGate:
    minimum_pairs = 30
    minimum_cases = 10
    minimum_families = 4
    required_tiers = (32_000, 128_000, 1_000_000)
    quality_non_inferiority_margin = 0.01
    minimum_recall = 0.98
    minimum_stale_rejection = 0.98
    minimum_evidence_precision = 0.95

    @staticmethod
    def _key(row: LongContextReceipt) -> tuple[str, int, str, int, str, str]:
        return row.case_id, row.tier_tokens, row.repository_hash, row.repetition, row.provider, row.model

    @classmethod
    def evaluate(cls, receipts: Iterable[LongContextReceipt]) -> dict[str, Any]:
        rows = list(receipts)
        reasons: list[str] = []
        invalid = [
            {"receipt_id": row.receipt_id, "reasons": list(row.validate())}
            for row in rows if row.validate()
        ]
        if invalid:
            reasons.append("invalid-receipts")
        if not rows:
            reasons.append("no-receipts")
        if any(row.synthetic for row in rows):
            reasons.append("synthetic-receipts-present")

        groups: dict[tuple[str, int, str, int, str, str], dict[str, LongContextReceipt]] = {}
        for row in rows:
            groups.setdefault(cls._key(row), {})[row.arm] = row
        pairs = [group for group in groups.values() if "baseline" in group and "syntavra" in group]
        if len(pairs) < cls.minimum_pairs:
            reasons.append("insufficient-paired-runs")

        cases = {pair["syntavra"].case_id for pair in pairs}
        families = {pair["syntavra"].task_family for pair in pairs}
        tiers = {pair["syntavra"].tier_tokens for pair in pairs}
        if len(cases) < cls.minimum_cases:
            reasons.append("insufficient-cases")
        if len(families) < cls.minimum_families:
            reasons.append("insufficient-task-families")
        if not set(cls.required_tiers).issubset(tiers):
            reasons.append("required-tiers-missing")

        quality_deltas: list[float] = []
        recalls: list[float] = []
        stale_rejections: list[float] = []
        precisions: list[float] = []
        token_ratios: list[float] = []
        wall_ratios: list[float] = []
        for pair in pairs:
            baseline = pair["baseline"]
            syntavra = pair["syntavra"]
            if baseline.provider != syntavra.provider or baseline.model != syntavra.model:
                reasons.append("provider-or-model-parity-failed")
                continue
            quality_deltas.append(syntavra.answer_quality - baseline.answer_quality)
            recalls.append(syntavra.required_fact_recall)
            stale_rejections.append(syntavra.stale_fact_rejection)
            precisions.append(syntavra.evidence_precision)
            baseline_tokens = baseline.input_tokens + baseline.output_tokens
            syntavra_tokens = syntavra.input_tokens + syntavra.output_tokens
            if baseline_tokens > 0:
                token_ratios.append(syntavra_tokens / baseline_tokens)
            if baseline.wall_time_ms > 0:
                wall_ratios.append(syntavra.wall_time_ms / baseline.wall_time_ms)
            if not syntavra.exact_recovery:
                reasons.append("exact-recovery-failed")
            if syntavra.forced_restart:
                reasons.append("forced-restart-observed")
            if syntavra.task_family == "cross-session-continuity" and not syntavra.continuity_restored:
                reasons.append("session-continuity-failed")

        mean_quality_delta = statistics.fmean(quality_deltas) if quality_deltas else -1.0
        mean_recall = statistics.fmean(recalls) if recalls else 0.0
        mean_stale = statistics.fmean(stale_rejections) if stale_rejections else 0.0
        mean_precision = statistics.fmean(precisions) if precisions else 0.0
        if mean_quality_delta < -cls.quality_non_inferiority_margin:
            reasons.append("quality-non-inferiority-failed")
        if mean_recall < cls.minimum_recall:
            reasons.append("required-fact-recall-failed")
        if mean_stale < cls.minimum_stale_rejection:
            reasons.append("stale-fact-rejection-failed")
        if mean_precision < cls.minimum_evidence_precision:
            reasons.append("evidence-precision-failed")

        ok = not reasons
        return {
            "ok": ok,
            "claim": "LONG_CONTEXT_QUALITY_VERIFIED" if ok else "LONG_CONTEXT_QUALITY_NOT_PROVEN",
            "architecture_claim": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
            "version": VERSION,
            "channel": CHANNEL,
            "reasons": sorted(set(reasons)),
            "invalid": invalid,
            "metrics": {
                "pairs": len(pairs),
                "cases": len(cases),
                "families": len(families),
                "tiers": sorted(tiers),
                "mean_quality_delta": mean_quality_delta if quality_deltas else None,
                "mean_required_fact_recall": mean_recall if recalls else None,
                "mean_stale_fact_rejection": mean_stale if stale_rejections else None,
                "mean_evidence_precision": mean_precision if precisions else None,
                "mean_token_ratio": statistics.fmean(token_ratios) if token_ratios else None,
                "mean_wall_time_ratio": statistics.fmean(wall_ratios) if wall_ratios else None,
            },
            "requirements": {
                "minimum_pairs": cls.minimum_pairs,
                "minimum_cases": cls.minimum_cases,
                "minimum_families": cls.minimum_families,
                "required_tiers": list(cls.required_tiers),
                "quality_non_inferiority_margin": cls.quality_non_inferiority_margin,
                "minimum_recall": cls.minimum_recall,
                "minimum_stale_rejection": cls.minimum_stale_rejection,
                "minimum_evidence_precision": cls.minimum_evidence_precision,
            },
        }


def manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "channel": CHANNEL,
        "name": "Syntavra Long-Context Quality Protocol",
        "style": "OOLONG-like evidence-intensive long-context evaluation",
        "tiers": list(LONG_CONTEXT_TIERS),
        "task_families": list(TASK_FAMILIES),
        "measured": [
            "answer quality",
            "required fact recall",
            "stale fact rejection",
            "evidence precision",
            "exact recovery",
            "forced restart",
            "session continuity",
            "provider tokens",
            "wall time",
        ],
        "claim_boundary": "A manifest or synthetic run never proves long-context quality.",
    }
