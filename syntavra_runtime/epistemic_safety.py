from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence

from .capability_security import CapabilityDecision
from .security_scan import scan_text
from .universal_context_item import UniversalContextItem


_TRUST_RANK = {"unknown": 0, "untrusted": 1, "observed": 2, "verified": 3}
_DECISIONS = {"ALLOW", "VERIFY", "ABSTAIN"}
_STATES = {"SUPPORTED", "UNCERTAIN", "MISSING", "CONTRADICTED", "TAINTED"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bounded(value: Any, *, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 6)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EvidenceRequirement:
    kind: str
    min_count: int = 1
    min_trust: str = "observed"
    fresh_required: bool = True
    exact_required: bool = False
    critical: bool = True

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("evidence requirement kind is required")
        if int(self.min_count) < 1:
            raise ValueError("evidence requirement min_count must be positive")
        if self.min_trust not in _TRUST_RANK:
            raise ValueError(f"unknown minimum trust level: {self.min_trust!r}")
        object.__setattr__(self, "kind", self.kind.strip())
        object.__setattr__(self, "min_count", int(self.min_count))


@dataclass(frozen=True)
class MinimumEvidenceSchema:
    schema_id: str
    action_class: str
    requirements: tuple[EvidenceRequirement, ...]
    irreversible: bool = False

    def __post_init__(self) -> None:
        if not self.schema_id.strip():
            raise ValueError("minimum evidence schema_id is required")
        if not self.action_class.strip():
            raise ValueError("minimum evidence action_class is required")
        if len({requirement.kind for requirement in self.requirements}) != len(self.requirements):
            raise ValueError("duplicate evidence requirement kinds are forbidden")
        object.__setattr__(self, "schema_id", self.schema_id.strip())
        object.__setattr__(self, "action_class", self.action_class.strip().casefold())
        object.__setattr__(self, "requirements", tuple(self.requirements))


@dataclass(frozen=True)
class ContextLease:
    lease_id: str
    dependencies: tuple[tuple[str, str], ...]
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if not self.lease_id.startswith("sha256:"):
            raise ValueError("context lease_id must be content addressed")
        normalized = tuple(sorted((str(item_id), str(content_hash)) for item_id, content_hash in self.dependencies))
        if not normalized:
            raise ValueError("context lease requires dependencies")
        if len({item_id for item_id, _ in normalized}) != len(normalized):
            raise ValueError("context lease dependency ids must be unique")
        object.__setattr__(self, "dependencies", normalized)
        if self.expires_at is not None:
            _parse_time(self.expires_at)


class EpistemicSafetyEngine:
    """Deterministic epistemic critic and fail-closed action gate.

    This component owns no evidence payloads and performs no mutations. It
    composes existing UniversalContextItem trust/freshness/taint semantics,
    SecurityScan ingress analysis, and CapabilitySecurity decisions into
    deterministic receipts suitable for replay and admission evidence.
    """

    def __init__(self, *, trusted_instruction_sources: Sequence[str] = ()):
        self.trusted_instruction_sources = tuple(
            sorted({str(value).strip() for value in trusted_instruction_sources if str(value).strip()})
        )

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "epistemic_state_engine": True,
            "context_critic": True,
            "missing_evidence_detection": True,
            "marginal_utility": True,
            "universal_taint_propagation": True,
            "instruction_data_separation": True,
            "prompt_injection_ingress_filter": True,
            "minimum_evidence_schema": True,
            "safe_action_commit_gate": True,
            "evidence_certificate": True,
            "agentic_abstention": True,
            "context_lease_invalidation": True,
            "universal_context_item_reused": True,
            "security_scan_reused": True,
            "capability_security_reused": True,
            "persistent_store": False,
            "side_effects": False,
            "public_cli_route": False,
        }

    def ingress(self, text: str, *, source: str, role: str = "data") -> dict[str, Any]:
        normalized_role = str(role or "data").strip().casefold()
        if normalized_role not in {"data", "instruction"}:
            raise ValueError("ingress role must be data or instruction")
        normalized_source = str(source).strip()
        if not normalized_source:
            raise ValueError("ingress source is required")

        scan = scan_text(str(text))
        trusted_instruction = (
            normalized_role == "instruction"
            and normalized_source in set(self.trusted_instruction_sources)
        )
        taint: list[str] = []
        if normalized_role == "instruction" and not trusted_instruction:
            taint.append("unauthorized-instruction")
        if scan.injection_risk and not trusted_instruction:
            taint.append("prompt-injection")
        if scan.confusable_risk and not trusted_instruction:
            taint.append("confusable-text")
        if scan.secret_types:
            taint.append("secret-bearing")
        if scan.pii_types:
            taint.append("pii-bearing")

        body = {
            "source": normalized_source,
            "role": normalized_role,
            "instruction_authority": trusted_instruction,
            "injection_risk": bool(scan.injection_risk),
            "injection_reasons": list(scan.injection_reasons),
            "secret_types": list(scan.secret_types),
            "pii_types": list(scan.pii_types),
            "high_entropy_tokens": int(scan.high_entropy_tokens),
            "taint": sorted(set(taint)),
            "redacted_preview": scan.redacted_text[:4096],
        }
        body["receipt_hash"] = _digest(body)
        return body

    def marginal_utility(
        self,
        item: UniversalContextItem,
        *,
        seen_content_hashes: Iterable[str] = (),
    ) -> float:
        seen = {str(value) for value in seen_content_hashes}
        relevance = _bounded(item.metadata.get("relevance"), default=0.5)
        trust = _bounded(item.trust.confidence, default=0.0)
        freshness = {
            "fresh": 1.0,
            "unknown": 0.55,
            "stale": 0.2,
            "expired": 0.0,
        }[item.freshness.state]
        novelty = 0.0 if item.content_sha256 in seen else 1.0
        exactness = 1.0 if item.representation == "exact" else 0.5
        score = (
            0.30 * relevance
            + 0.30 * trust
            + 0.15 * freshness
            + 0.15 * novelty
            + 0.10 * exactness
        )
        return round(max(0.0, min(1.0, score)), 6)

    @staticmethod
    def create_lease(
        items: Iterable[UniversalContextItem],
        *,
        expires_at: str | None = None,
    ) -> ContextLease:
        dependencies = tuple(
            sorted((item.item_id, item.content_sha256) for item in items)
        )
        if not dependencies:
            raise ValueError("cannot create a context lease without dependencies")
        body = {"dependencies": dependencies, "expires_at": expires_at}
        return ContextLease(
            lease_id=_digest(body),
            dependencies=dependencies,
            expires_at=expires_at,
        )

    @staticmethod
    def validate_lease(
        lease: ContextLease,
        items: Iterable[UniversalContextItem],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
        indexed = {item.item_id: item for item in items}
        reasons: list[str] = []
        missing: list[str] = []
        changed: list[str] = []
        stale: list[str] = []

        expiry = _parse_time(lease.expires_at)
        if expiry is not None and current > expiry:
            reasons.append("LEASE_EXPIRED")

        for item_id, content_hash in lease.dependencies:
            item = indexed.get(item_id)
            if item is None:
                missing.append(item_id)
                continue
            if item.content_sha256 != content_hash or not item.verify_integrity():
                changed.append(item_id)
            if item.freshness.state in {"stale", "expired"}:
                stale.append(item_id)

        if missing:
            reasons.append("DEPENDENCY_MISSING")
        if changed:
            reasons.append("DEPENDENCY_CHANGED")
        if stale:
            reasons.append("DEPENDENCY_STALE")

        body = {
            "lease_id": lease.lease_id,
            "valid": not reasons,
            "reasons": sorted(set(reasons)),
            "missing": sorted(missing),
            "changed": sorted(changed),
            "stale": sorted(stale),
        }
        body["receipt_hash"] = _digest(body)
        return body

    def _item_analysis(self, item: UniversalContextItem) -> dict[str, Any]:
        role = str(item.metadata.get("role", "data"))
        ingress = self.ingress(
            _content_text(item.content),
            source=item.provenance.source,
            role=role,
        )
        taint = sorted(set((*item.trust.taint, *ingress["taint"])))
        return {
            "item_id": item.item_id,
            "kind": item.kind,
            "representation": item.representation,
            "integrity_ok": item.verify_integrity(),
            "trust_level": item.trust.level,
            "trust_confidence": item.trust.confidence,
            "freshness": item.freshness.state,
            "taint": taint,
            "instruction_authority": ingress["instruction_authority"],
            "injection_risk": ingress["injection_risk"],
            "ingress_receipt": ingress["receipt_hash"],
            "marginal_utility": self.marginal_utility(item),
        }

    def critic(
        self,
        items: Iterable[UniversalContextItem],
        schema: MinimumEvidenceSchema,
        *,
        conflicts: Iterable[tuple[str, str]] = (),
        lease: ContextLease | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        materialized = tuple(items)
        analyses = [self._item_analysis(item) for item in materialized]
        indexed = {item.item_id: item for item in materialized}
        missing: list[dict[str, Any]] = []

        for requirement in schema.requirements:
            count = 0
            for item in materialized:
                if item.kind != requirement.kind:
                    continue
                if not item.verify_integrity():
                    continue
                if _TRUST_RANK[item.trust.level] < _TRUST_RANK[requirement.min_trust]:
                    continue
                if requirement.fresh_required and item.freshness.state != "fresh":
                    continue
                if requirement.exact_required and item.representation != "exact":
                    continue
                count += 1
            if count < requirement.min_count:
                missing.append(
                    {
                        "kind": requirement.kind,
                        "required": requirement.min_count,
                        "observed": count,
                        "critical": requirement.critical,
                    }
                )

        conflict_rows = sorted(
            {
                tuple(sorted((str(left), str(right))))
                for left, right in conflicts
                if str(left) and str(right) and str(left) != str(right)
            }
        )
        critical_missing = [row for row in missing if row["critical"]]
        tainted = sorted(
            row["item_id"]
            for row in analyses
            if row["taint"] or not row["integrity_ok"]
        )
        injection = sorted(row["item_id"] for row in analyses if row["injection_risk"])
        stale = sorted(
            item.item_id
            for item in materialized
            if item.freshness.state in {"stale", "expired"}
        )
        lease_report = (
            self.validate_lease(lease, materialized, now=now)
            if lease is not None
            else {"valid": True, "reasons": [], "receipt_hash": None}
        )

        if tainted:
            state = "TAINTED"
        elif conflict_rows:
            state = "CONTRADICTED"
        elif critical_missing:
            state = "MISSING"
        elif missing or stale or not lease_report["valid"]:
            state = "UNCERTAIN"
        else:
            state = "SUPPORTED"
        assert state in _STATES

        reasons: list[str] = []
        decision = "ALLOW"
        if critical_missing:
            decision = "ABSTAIN"
            reasons.append("CRITICAL_EVIDENCE_MISSING")
        if conflict_rows:
            decision = "ABSTAIN" if schema.irreversible else "VERIFY"
            reasons.append("EVIDENCE_CONFLICT")
        if injection:
            decision = "ABSTAIN" if schema.irreversible else "VERIFY"
            reasons.append("UNTRUSTED_INSTRUCTION_INGRESS")
        if tainted and schema.irreversible:
            decision = "ABSTAIN"
            reasons.append("TAINTED_IRREVERSIBLE_ACTION")
        elif tainted and decision == "ALLOW":
            decision = "VERIFY"
            reasons.append("TAINT_REQUIRES_VERIFICATION")
        if not lease_report["valid"]:
            decision = "ABSTAIN" if schema.irreversible else "VERIFY"
            reasons.append("DEPENDENCY_LEASE_INVALID")
        if missing and not critical_missing and decision == "ALLOW":
            decision = "VERIFY"
            reasons.append("NONCRITICAL_EVIDENCE_MISSING")
        if stale and decision == "ALLOW":
            decision = "VERIFY"
            reasons.append("STALE_EVIDENCE")
        assert decision in _DECISIONS

        body = {
            "schema_id": schema.schema_id,
            "action_class": schema.action_class,
            "irreversible": schema.irreversible,
            "state": state,
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
            "evidence_ids": sorted(indexed),
            "missing": missing,
            "tainted_item_ids": tainted,
            "injection_item_ids": injection,
            "stale_item_ids": stale,
            "conflicts": [list(row) for row in conflict_rows],
            "lease": lease_report,
            "items": analyses,
        }
        body["certificate_hash"] = _digest(body)
        return body

    def gate_action(
        self,
        *,
        schema: MinimumEvidenceSchema,
        items: Iterable[UniversalContextItem],
        capability_decision: CapabilityDecision | None,
        conflicts: Iterable[tuple[str, str]] = (),
        lease: ContextLease | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        critic = self.critic(items, schema, conflicts=conflicts, lease=lease, now=now)
        decision = critic["decision"]
        reasons = list(critic["reason_codes"])

        capability_payload: dict[str, Any] | None = None
        mutating_class = schema.action_class in {"write", "execute", "network", "commit", "deploy", "publish"}
        if capability_decision is None:
            if mutating_class:
                decision = "ABSTAIN"
                reasons.append("CAPABILITY_DECISION_REQUIRED")
        else:
            capability_payload = asdict(capability_decision)
            expected = "write" if schema.action_class == "commit" else schema.action_class
            if expected in {"write", "execute", "network", "read"} and capability_decision.category != expected:
                decision = "ABSTAIN"
                reasons.append("CAPABILITY_CATEGORY_MISMATCH")
            if not capability_decision.allowed:
                decision = "ABSTAIN"
                reasons.append("CAPABILITY_DENIED")

        body = {
            "schema_id": schema.schema_id,
            "action_class": schema.action_class,
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
            "critic_certificate": critic["certificate_hash"],
            "evidence_ids": critic["evidence_ids"],
            "capability": capability_payload,
        }
        body["certificate_hash"] = _digest(body)
        return body
