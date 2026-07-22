from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .integration_matrix import IntegrationMatrix
from .release_identity import CHANNEL, VERSION


_HASH = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class LiveIntegrationReceipt:
    receipt_id: str
    integration_id: str
    family: str
    observed_at: str
    syntavra_version: str
    syntavra_channel: str
    adapter_version: str
    operating_system: str
    runtime_version: str
    environment_hash: str
    config_hash: str
    harness_commit: str
    artifact_hash: str
    install_succeeded: bool
    doctor_passed: bool
    request_succeeded: bool
    response_succeeded: bool
    streaming_verified: bool
    provider_usage_captured: bool
    tool_routing_verified: bool
    session_continuity_verified: bool
    rollback_verified: bool
    external: bool
    synthetic: bool
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LiveIntegrationReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            integration_id=str(value.get("integration_id", "")),
            family=str(value.get("family", "")),
            observed_at=str(value.get("observed_at", "")),
            syntavra_version=str(value.get("syntavra_version", "")),
            syntavra_channel=str(value.get("syntavra_channel", "")),
            adapter_version=str(value.get("adapter_version", "")),
            operating_system=str(value.get("operating_system", "")),
            runtime_version=str(value.get("runtime_version", "")),
            environment_hash=str(value.get("environment_hash", "")),
            config_hash=str(value.get("config_hash", "")),
            harness_commit=str(value.get("harness_commit", "")),
            artifact_hash=str(value.get("artifact_hash", "")),
            install_succeeded=bool(value.get("install_succeeded", False)),
            doctor_passed=bool(value.get("doctor_passed", False)),
            request_succeeded=bool(value.get("request_succeeded", False)),
            response_succeeded=bool(value.get("response_succeeded", False)),
            streaming_verified=bool(value.get("streaming_verified", False)),
            provider_usage_captured=bool(value.get("provider_usage_captured", False)),
            tool_routing_verified=bool(value.get("tool_routing_verified", False)),
            session_continuity_verified=bool(value.get("session_continuity_verified", False)),
            rollback_verified=bool(value.get("rollback_verified", False)),
            external=bool(value.get("external", False)),
            synthetic=bool(value.get("synthetic", True)),
            metadata=dict(value.get("metadata") or {}),
        )

    def validate(self) -> tuple[str, ...]:
        reasons: list[str] = []
        try:
            spec = IntegrationMatrix.by_id(self.integration_id)
        except KeyError:
            spec = None
            reasons.append("unknown-integration")
        if spec and spec.family != self.family:
            reasons.append("integration-family-mismatch")
        if self.family not in {"provider", "framework", "host"}:
            reasons.append("invalid-family")
        for name in ("receipt_id", "integration_id", "observed_at", "adapter_version", "operating_system", "runtime_version"):
            if not getattr(self, name):
                reasons.append(f"missing-{name.replace('_', '-')}")
        try:
            parsed = dt.datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                reasons.append("observed-at-missing-timezone")
        except ValueError:
            reasons.append("invalid-observed-at")
        if self.syntavra_version != VERSION:
            reasons.append("version-mismatch")
        if self.syntavra_channel != CHANNEL:
            reasons.append("channel-mismatch")
        for name in ("environment_hash", "config_hash", "artifact_hash"):
            if not _HASH.fullmatch(str(getattr(self, name))):
                reasons.append(f"invalid-{name.replace('_', '-')}")
        if not _COMMIT.fullmatch(self.harness_commit):
            reasons.append("invalid-harness-commit")
        if not self.external:
            reasons.append("not-external")
        if self.synthetic:
            reasons.append("synthetic-receipt")
        required = {
            "install-succeeded": self.install_succeeded,
            "doctor-passed": self.doctor_passed,
            "request-succeeded": self.request_succeeded,
            "response-succeeded": self.response_succeeded,
            "rollback-verified": self.rollback_verified,
        }
        if self.family == "provider":
            required["provider-usage-captured"] = self.provider_usage_captured
            required["streaming-verified"] = self.streaming_verified
        if self.family == "framework":
            required["provider-usage-captured"] = self.provider_usage_captured
        if self.family == "host":
            required["tool-routing-verified"] = self.tool_routing_verified
            required["session-continuity-verified"] = self.session_continuity_verified
        reasons.extend(f"{name}-required" for name, passed in required.items() if not passed)
        return tuple(dict.fromkeys(reasons))


class LiveCertificationGate:
    minimum_receipts_per_integration = 3
    minimum_operating_systems = 2

    @staticmethod
    def load_rows(value: Iterable[Mapping[str, Any]]) -> list[LiveIntegrationReceipt]:
        return [LiveIntegrationReceipt.from_mapping(item) for item in value]

    @classmethod
    def evaluate(
        cls,
        receipts: Iterable[LiveIntegrationReceipt],
        *,
        integration_id: str | None = None,
    ) -> dict[str, Any]:
        rows = [row for row in receipts if integration_id is None or row.integration_id == integration_id]
        invalid = [{"receipt_id": row.receipt_id, "reasons": list(row.validate())} for row in rows if row.validate()]
        groups: dict[str, list[LiveIntegrationReceipt]] = {}
        for row in rows:
            groups.setdefault(row.integration_id, []).append(row)
        certified: list[str] = []
        pending: dict[str, list[str]] = {}
        for item_id, items in sorted(groups.items()):
            reasons: list[str] = []
            valid = [row for row in items if not row.validate()]
            if len(valid) < cls.minimum_receipts_per_integration:
                reasons.append("insufficient-live-receipts")
            systems = {row.operating_system for row in valid}
            if len(systems) < cls.minimum_operating_systems:
                reasons.append("insufficient-operating-system-diversity")
            harnesses = {row.harness_commit for row in valid}
            if len(harnesses) != 1:
                reasons.append("harness-commit-not-pinned")
            if reasons:
                pending[item_id] = reasons
            else:
                certified.append(item_id)
        if integration_id and integration_id not in groups:
            pending[integration_id] = ["no-live-receipts"]
        ok = bool(certified) and not invalid and not pending
        return {
            "ok": ok,
            "claim": "LIVE_INTEGRATION_CERTIFIED" if ok else "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN",
            "version": VERSION,
            "channel": CHANNEL,
            "certified_integrations": certified,
            "pending": pending,
            "invalid": invalid,
            "metrics": {
                "receipts": len(rows),
                "valid_receipts": len(rows) - len(invalid),
                "integrations_observed": len(groups),
                "integrations_certified": len(certified),
            },
            "requirements": {
                "minimum_receipts_per_integration": cls.minimum_receipts_per_integration,
                "minimum_operating_systems": cls.minimum_operating_systems,
                "pinned_harness_commit": True,
                "external_non_synthetic": True,
            },
        }

    @classmethod
    def certification_manifest(cls, receipts: Iterable[LiveIntegrationReceipt]) -> dict[str, Any]:
        rows = list(receipts)
        certified = set(cls.evaluate(rows)["certified_integrations"])
        records: list[dict[str, Any]] = []
        for integration in IntegrationMatrix.records():
            records.append({
                **integration,
                "live_certified": integration["integration_id"] in certified,
                "certification": "VERIFIED_LIVE" if integration["integration_id"] in certified else "internal-contract",
            })
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "matrix": IntegrationMatrix.validate(),
            "integrations": records,
            "live_certified": sorted(certified),
        }
