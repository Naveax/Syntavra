from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .release_identity import CHANNEL, VERSION
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class WorkloadSpec:
    workload_id: str
    family: str
    quality_verifier: str
    repetitions: int = 30
    competitor_arms: tuple[str, ...] = ("baseline", "syntavra", "headroom", "context-mode", "token-savior", "volt-lcm")
    requires_provider_receipt: bool = True


WORKLOADS: tuple[WorkloadSpec, ...] = tuple(
    WorkloadSpec(name, family, verifier)
    for name, family, verifier in (
        ("code-search", "coding", "symbol-and-answer-verifier"),
        ("repository-exploration", "coding", "repository-map-verifier"),
        ("large-build-log", "tool-output", "failure-root-cause-verifier"),
        ("test-failure-triage", "coding", "test-repair-verifier"),
        ("sre-incident", "operations", "timeline-and-remediation-verifier"),
        ("github-issue-triage", "operations", "classification-verifier"),
        ("sql-analytics", "structured-data", "query-answer-verifier"),
        ("rag-qa", "retrieval", "citation-grounding-verifier"),
        ("api-response-analysis", "structured-data", "schema-and-answer-verifier"),
        ("multi-agent-handoff", "agents", "state-continuity-verifier"),
        ("long-coding-session", "long-context", "project-goal-verifier"),
        ("multimodal-document", "multimodal", "cross-modal-grounding-verifier"),
    )
)


@dataclass(frozen=True)
class DistributionTarget:
    channel: str
    artifact: str
    signed: bool
    provenance: bool
    status: str = "configured"


DISTRIBUTIONS: tuple[DistributionTarget, ...] = (
    DistributionTarget("pypi", "syntavra-runtime", True, True),
    DistributionTarget("npm", "@syntavra/sdk", True, True),
    DistributionTarget("ghcr", "syntavra/runtime", True, True),
    DistributionTarget("homebrew", "syntavra", True, True),
    DistributionTarget("winget", "Syntavra.Syntavra", True, True),
    DistributionTarget("standalone", "windows-linux-macos", True, True),
)


@dataclass(frozen=True)
class BetaReceipt:
    receipt_id: str
    observed_at: str
    repository_hash: str
    user_hash: str
    workload_id: str
    success: bool
    crash_free: bool
    latency_ms: float
    provider_receipt: bool
    synthetic: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class PublicProofGate:
    minimum_days = 90
    minimum_receipts = 1000
    minimum_repositories = 100
    minimum_users = 50
    minimum_crash_free = 0.999
    minimum_success = 0.99

    @staticmethod
    def workload_manifest() -> dict[str, Any]:
        rows = [asdict(item) for item in WORKLOADS]
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "workloads": rows,
            "workload_count": len(rows),
            "manifest_hash": sha256_bytes(canonical_json(rows)),
        }

    @classmethod
    def evaluate_beta(cls, receipts: Iterable[BetaReceipt], *, now: dt.datetime | None = None) -> dict[str, Any]:
        rows = list(receipts)
        now = now or dt.datetime.now(dt.timezone.utc)
        reasons: list[str] = []
        live = [row for row in rows if not row.synthetic and row.provider_receipt]
        if len(live) < cls.minimum_receipts:
            reasons.append("insufficient-live-receipts")
        repositories = {row.repository_hash for row in live}
        users = {row.user_hash for row in live}
        if len(repositories) < cls.minimum_repositories:
            reasons.append("insufficient-repositories")
        if len(users) < cls.minimum_users:
            reasons.append("insufficient-users")
        observed: list[dt.datetime] = []
        for row in live:
            try:
                value = dt.datetime.fromisoformat(row.observed_at.replace("Z", "+00:00"))
                observed.append(value.astimezone(dt.timezone.utc))
            except ValueError:
                reasons.append(f"invalid-time:{row.receipt_id}")
        days = (now - min(observed)).total_seconds() / 86400 if observed else 0.0
        if days < cls.minimum_days:
            reasons.append("insufficient-observation-window")
        crash_free = sum(1 for row in live if row.crash_free) / max(1, len(live))
        success = sum(1 for row in live if row.success) / max(1, len(live))
        if crash_free < cls.minimum_crash_free:
            reasons.append("crash-free-target-missed")
        if success < cls.minimum_success:
            reasons.append("success-target-missed")
        return {
            "ok": not reasons,
            "claim": "PUBLIC_PRODUCT_MATURITY_VERIFIED" if not reasons else "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
            "reasons": reasons,
            "metrics": {
                "live_receipts": len(live), "repositories": len(repositories), "users": len(users),
                "days": days, "crash_free": crash_free, "success": success,
            },
        }

    @staticmethod
    def release_readiness(*, sbom: bool, provenance: bool, reproducible_build: bool, signed_tags: bool, migration_guides: bool, rollback: bool) -> dict[str, Any]:
        checks = {
            "sbom": sbom,
            "provenance": provenance,
            "reproducible_build": reproducible_build,
            "signed_tags": signed_tags,
            "migration_guides": migration_guides,
            "rollback": rollback,
            "version_locked_0_0_1": True,
            "pre_release_channel": True,
        }
        return {
            "ok": all(checks.values()),
            "version": VERSION,
            "channel": CHANNEL,
            "checks": checks,
            "distributions": [asdict(item) for item in DISTRIBUTIONS],
        }


def write_prerelease_manifest(path: Path) -> dict[str, Any]:
    value = {
        "version": VERSION,
        "channel": CHANNEL,
        "publish_as_prerelease": True,
        "stable": False,
        "version_locked": True,
        "workloads": PublicProofGate.workload_manifest(),
        "distributions": [asdict(item) for item in DISTRIBUTIONS],
        "claim_boundaries": {
            "competitor_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "public_product_maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
            "infinite_context": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
        },
    }
    value["manifest_hash"] = sha256_bytes(canonical_json(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return value
