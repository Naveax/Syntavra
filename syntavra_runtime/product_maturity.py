from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .release_identity import CHANNEL, VERSION


@dataclass(frozen=True)
class OnboardingReceipt:
    receipt_id: str
    observed_at: str
    user_hash: str
    repository_hash: str
    integration_id: str
    operating_system: str
    install_wall_time_ms: float
    success: bool
    rollback_verified: bool
    doctor_passed: bool
    synthetic: bool
    version: str = VERSION
    channel: str = CHANNEL

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OnboardingReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            observed_at=str(value.get("observed_at", "")),
            user_hash=str(value.get("user_hash", "")),
            repository_hash=str(value.get("repository_hash", "")),
            integration_id=str(value.get("integration_id", "")),
            operating_system=str(value.get("operating_system", "")),
            install_wall_time_ms=float(value.get("install_wall_time_ms", -1)),
            success=bool(value.get("success", False)),
            rollback_verified=bool(value.get("rollback_verified", False)),
            doctor_passed=bool(value.get("doctor_passed", False)),
            synthetic=bool(value.get("synthetic", True)),
            version=str(value.get("version", VERSION)),
            channel=str(value.get("channel", CHANNEL)),
        )


@dataclass(frozen=True)
class DistributionReceipt:
    receipt_id: str
    observed_at: str
    channel_name: str
    package_name: str
    version: str
    downloads: int
    unique_installations: int
    source_verified: bool
    synthetic: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DistributionReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            observed_at=str(value.get("observed_at", "")),
            channel_name=str(value.get("channel_name", "")),
            package_name=str(value.get("package_name", "")),
            version=str(value.get("version", "")),
            downloads=int(value.get("downloads", -1)),
            unique_installations=int(value.get("unique_installations", -1)),
            source_verified=bool(value.get("source_verified", False)),
            synthetic=bool(value.get("synthetic", True)),
        )


@dataclass(frozen=True)
class ReleaseReceipt:
    receipt_id: str
    published_at: str
    artifact_id: str
    version: str
    channel: str
    signed: bool
    provenance: bool
    source_verified: bool
    synthetic: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            published_at=str(value.get("published_at", "")),
            artifact_id=str(value.get("artifact_id", "")),
            version=str(value.get("version", "")),
            channel=str(value.get("channel", "")),
            signed=bool(value.get("signed", False)),
            provenance=bool(value.get("provenance", False)),
            source_verified=bool(value.get("source_verified", False)),
            synthetic=bool(value.get("synthetic", True)),
        )


def _time(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


class ProductMaturityGate:
    minimum_days = 90
    minimum_onboarding_receipts = 1000
    minimum_users = 50
    minimum_repositories = 100
    minimum_integrations = 5
    minimum_operating_systems = 3
    minimum_onboarding_success = 0.99
    maximum_p95_install_seconds = 60.0
    minimum_distribution_channels = 2
    minimum_public_downloads = 1000
    minimum_unique_installations = 250
    minimum_verified_releases = 4
    maximum_release_gap_days = 45.0

    @classmethod
    def evaluate(
        cls,
        onboarding: Iterable[OnboardingReceipt],
        distributions: Iterable[DistributionReceipt],
        releases: Iterable[ReleaseReceipt],
        *,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        onboarding_rows = list(onboarding)
        distribution_rows = list(distributions)
        release_rows = list(releases)
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        reasons: list[str] = []

        if any(item.synthetic for item in [*onboarding_rows, *distribution_rows, *release_rows]):
            reasons.append("synthetic-receipts-present")

        live_onboarding = [
            row for row in onboarding_rows
            if not row.synthetic and row.version == VERSION and row.channel == CHANNEL and _time(row.observed_at)
        ]
        if len(live_onboarding) < cls.minimum_onboarding_receipts:
            reasons.append("insufficient-onboarding-receipts")
        users = {row.user_hash for row in live_onboarding if row.user_hash}
        repositories = {row.repository_hash for row in live_onboarding if row.repository_hash}
        integrations = {row.integration_id for row in live_onboarding if row.integration_id}
        operating_systems = {row.operating_system for row in live_onboarding if row.operating_system}
        if len(users) < cls.minimum_users:
            reasons.append("insufficient-users")
        if len(repositories) < cls.minimum_repositories:
            reasons.append("insufficient-repositories")
        if len(integrations) < cls.minimum_integrations:
            reasons.append("insufficient-live-integrations")
        if len(operating_systems) < cls.minimum_operating_systems:
            reasons.append("insufficient-operating-system-coverage")

        success = sum(row.success and row.doctor_passed for row in live_onboarding) / max(1, len(live_onboarding))
        rollback = sum(row.rollback_verified for row in live_onboarding) / max(1, len(live_onboarding))
        install_times = [row.install_wall_time_ms for row in live_onboarding if row.install_wall_time_ms >= 0]
        p95_install_ms = _percentile(install_times, 0.95)
        if success < cls.minimum_onboarding_success:
            reasons.append("onboarding-success-target-missed")
        if rollback < cls.minimum_onboarding_success:
            reasons.append("rollback-verification-target-missed")
        if p95_install_ms > cls.maximum_p95_install_seconds * 1000:
            reasons.append("installation-speed-target-missed")

        verified_distributions = [
            row for row in distribution_rows
            if not row.synthetic and row.source_verified and row.version == VERSION and _time(row.observed_at)
        ]
        channels = {row.channel_name for row in verified_distributions if row.channel_name}
        public_downloads = sum(max(0, row.downloads) for row in verified_distributions)
        unique_installations = sum(max(0, row.unique_installations) for row in verified_distributions)
        if len(channels) < cls.minimum_distribution_channels:
            reasons.append("insufficient-distribution-channels")
        if public_downloads < cls.minimum_public_downloads:
            reasons.append("insufficient-public-downloads")
        if unique_installations < cls.minimum_unique_installations:
            reasons.append("insufficient-unique-installations")

        verified_releases = sorted(
            (
                row for row in release_rows
                if not row.synthetic and row.source_verified and row.signed and row.provenance
                and row.version == VERSION and row.channel == CHANNEL and _time(row.published_at)
            ),
            key=lambda row: _time(row.published_at) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        )
        if len(verified_releases) < cls.minimum_verified_releases:
            reasons.append("insufficient-verified-releases")
        release_times = [_time(row.published_at) for row in verified_releases]
        valid_release_times = [value for value in release_times if value]
        gaps = [
            (right - left).total_seconds() / 86400
            for left, right in zip(valid_release_times, valid_release_times[1:])
        ]
        max_gap_days = max(gaps, default=0.0)
        if max_gap_days > cls.maximum_release_gap_days:
            reasons.append("release-cadence-gap-too-large")

        all_times = [
            value for value in (
                *(_time(row.observed_at) for row in live_onboarding),
                *(_time(row.observed_at) for row in verified_distributions),
                *(_time(row.published_at) for row in verified_releases),
            ) if value
        ]
        days = (now - min(all_times)).total_seconds() / 86400 if all_times else 0.0
        if days < cls.minimum_days:
            reasons.append("insufficient-operation-window")

        ok = not reasons
        return {
            "ok": ok,
            "claim": "PUBLIC_PRODUCT_MATURITY_VERIFIED" if ok else "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
            "version": VERSION,
            "channel": CHANNEL,
            "reasons": sorted(set(reasons)),
            "metrics": {
                "days": days,
                "onboarding_receipts": len(live_onboarding),
                "users": len(users),
                "repositories": len(repositories),
                "live_integrations": len(integrations),
                "operating_systems": len(operating_systems),
                "onboarding_success": success,
                "rollback_verified": rollback,
                "mean_install_wall_time_ms": statistics.fmean(install_times) if install_times else None,
                "p95_install_wall_time_ms": p95_install_ms if install_times else None,
                "distribution_channels": len(channels),
                "public_downloads": public_downloads,
                "unique_installations": unique_installations,
                "verified_releases": len(verified_releases),
                "maximum_release_gap_days": max_gap_days,
            },
            "requirements": {
                "minimum_days": cls.minimum_days,
                "minimum_onboarding_receipts": cls.minimum_onboarding_receipts,
                "minimum_users": cls.minimum_users,
                "minimum_repositories": cls.minimum_repositories,
                "minimum_integrations": cls.minimum_integrations,
                "minimum_operating_systems": cls.minimum_operating_systems,
                "minimum_onboarding_success": cls.minimum_onboarding_success,
                "maximum_p95_install_seconds": cls.maximum_p95_install_seconds,
                "minimum_distribution_channels": cls.minimum_distribution_channels,
                "minimum_public_downloads": cls.minimum_public_downloads,
                "minimum_unique_installations": cls.minimum_unique_installations,
                "minimum_verified_releases": cls.minimum_verified_releases,
                "maximum_release_gap_days": cls.maximum_release_gap_days,
            },
        }


def load_maturity_document(value: Mapping[str, Any]) -> tuple[list[OnboardingReceipt], list[DistributionReceipt], list[ReleaseReceipt]]:
    onboarding = [OnboardingReceipt.from_mapping(item) for item in value.get("onboarding", []) if isinstance(item, Mapping)]
    distributions = [DistributionReceipt.from_mapping(item) for item in value.get("distributions", []) if isinstance(item, Mapping)]
    releases = [ReleaseReceipt.from_mapping(item) for item in value.get("releases", []) if isinstance(item, Mapping)]
    return onboarding, distributions, releases
