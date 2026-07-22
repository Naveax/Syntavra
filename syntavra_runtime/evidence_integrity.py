from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .external_benchmarks import ExternalBenchmarkReceipt
from .live_certification import LiveIntegrationReceipt
from .release_identity import CHANNEL, VERSION


@dataclass(frozen=True)
class EvidenceIntegrityResult:
    ok: bool
    version: str
    channel: str
    reasons: tuple[str, ...]
    metrics: dict[str, Any]
    duplicates: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_timestamp(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(str(value) for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


class ExternalEvidenceIntegrityGate:
    """Additional fail-closed integrity checks for claim-bearing external evidence.

    This gate never upgrades a benchmark or certification result. It only rejects
    duplicated, stale, future-dated, or non-independent receipt collections.
    """

    maximum_future_skew = dt.timedelta(minutes=10)
    maximum_receipt_age = dt.timedelta(days=365)

    @classmethod
    def benchmark_receipts(
        cls,
        receipts: Iterable[ExternalBenchmarkReceipt],
        *,
        observed_at: Mapping[str, str],
        now: dt.datetime | None = None,
    ) -> EvidenceIntegrityResult:
        rows = list(receipts)
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        reasons: list[str] = []
        duplicate_receipt_ids = _duplicates(row.receipt_id for row in rows)
        duplicate_pair_arms = _duplicates(
            "|".join((
                row.suite_id,
                row.task_id,
                str(row.repetition),
                row.dataset_version,
                row.harness_commit,
                row.verifier_commit,
                row.provider,
                row.model,
                row.arm,
            ))
            for row in rows
        )
        duplicate_artifacts = _duplicates(row.result_artifact_hash for row in rows)
        duplicate_provider_receipts = _duplicates(row.raw_provider_receipt_hash for row in rows)
        missing_timestamps: list[str] = []
        invalid_timestamps: list[str] = []
        future_receipts: list[str] = []
        stale_receipts: list[str] = []
        for row in rows:
            raw = str(observed_at.get(row.receipt_id, ""))
            if not raw:
                missing_timestamps.append(row.receipt_id)
                continue
            parsed = _utc_timestamp(raw)
            if parsed is None:
                invalid_timestamps.append(row.receipt_id)
                continue
            if parsed > now + cls.maximum_future_skew:
                future_receipts.append(row.receipt_id)
            if now - parsed > cls.maximum_receipt_age:
                stale_receipts.append(row.receipt_id)

        if duplicate_receipt_ids:
            reasons.append("duplicate-receipt-ids")
        if duplicate_pair_arms:
            reasons.append("duplicate-pair-arm-runs")
        if duplicate_artifacts:
            reasons.append("duplicate-result-artifacts")
        if duplicate_provider_receipts:
            reasons.append("duplicate-provider-receipts")
        if missing_timestamps:
            reasons.append("missing-observed-at")
        if invalid_timestamps:
            reasons.append("invalid-observed-at")
        if future_receipts:
            reasons.append("future-dated-receipts")
        if stale_receipts:
            reasons.append("stale-receipts")

        duplicates = {
            "receipt_ids": duplicate_receipt_ids,
            "pair_arms": duplicate_pair_arms,
            "result_artifacts": duplicate_artifacts,
            "provider_receipts": duplicate_provider_receipts,
            "missing_timestamps": sorted(missing_timestamps),
            "invalid_timestamps": sorted(invalid_timestamps),
            "future_receipts": sorted(future_receipts),
            "stale_receipts": sorted(stale_receipts),
        }
        return EvidenceIntegrityResult(
            ok=not reasons and bool(rows),
            version=VERSION,
            channel=CHANNEL,
            reasons=tuple(sorted(set(reasons or (["no-receipts"] if not rows else [])))),
            metrics={
                "receipts": len(rows),
                "unique_tasks": len({(row.suite_id, row.task_id) for row in rows}),
                "unique_result_artifacts": len({row.result_artifact_hash for row in rows}),
                "unique_provider_receipts": len({row.raw_provider_receipt_hash for row in rows}),
            },
            duplicates=duplicates,
        )

    @classmethod
    def live_certification_receipts(
        cls,
        receipts: Iterable[LiveIntegrationReceipt],
        *,
        now: dt.datetime | None = None,
    ) -> EvidenceIntegrityResult:
        rows = list(receipts)
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        reasons: list[str] = []
        duplicate_receipt_ids = _duplicates(row.receipt_id for row in rows)
        duplicate_artifacts = _duplicates(row.artifact_hash for row in rows)
        duplicate_environments = _duplicates(
            "|".join((row.integration_id, row.environment_hash, row.config_hash, row.operating_system))
            for row in rows
        )
        invalid_timestamps: list[str] = []
        future_receipts: list[str] = []
        stale_receipts: list[str] = []
        for row in rows:
            parsed = _utc_timestamp(row.observed_at)
            if parsed is None:
                invalid_timestamps.append(row.receipt_id)
                continue
            if parsed > now + cls.maximum_future_skew:
                future_receipts.append(row.receipt_id)
            if now - parsed > cls.maximum_receipt_age:
                stale_receipts.append(row.receipt_id)

        if duplicate_receipt_ids:
            reasons.append("duplicate-receipt-ids")
        if duplicate_artifacts:
            reasons.append("duplicate-certification-artifacts")
        if duplicate_environments:
            reasons.append("duplicate-certification-environments")
        if invalid_timestamps:
            reasons.append("invalid-observed-at")
        if future_receipts:
            reasons.append("future-dated-receipts")
        if stale_receipts:
            reasons.append("stale-receipts")

        duplicates = {
            "receipt_ids": duplicate_receipt_ids,
            "artifacts": duplicate_artifacts,
            "environments": duplicate_environments,
            "invalid_timestamps": sorted(invalid_timestamps),
            "future_receipts": sorted(future_receipts),
            "stale_receipts": sorted(stale_receipts),
        }
        return EvidenceIntegrityResult(
            ok=not reasons and bool(rows),
            version=VERSION,
            channel=CHANNEL,
            reasons=tuple(sorted(set(reasons or (["no-receipts"] if not rows else [])))),
            metrics={
                "receipts": len(rows),
                "integrations": len({row.integration_id for row in rows}),
                "operating_systems": len({row.operating_system for row in rows}),
                "unique_artifacts": len({row.artifact_hash for row in rows}),
                "unique_environments": len({(row.environment_hash, row.config_hash, row.operating_system) for row in rows}),
            },
            duplicates=duplicates,
        )
