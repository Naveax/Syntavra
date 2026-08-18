from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_REPRESENTATIONS = {"exact", "structural", "semantic", "bounded-preview"}
_TRUST_LEVELS = {"unknown", "untrusted", "observed", "verified"}
_FRESHNESS_STATES = {"unknown", "fresh", "stale", "expired"}
_RECOVERY_KINDS = {"file-range", "evidence-node", "evidence-edge", "artifact", "memory", "tool-result"}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_digest(value: str) -> str:
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"invalid sha256 digest: {value!r}")
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def _json_clone(value: Any) -> Any:
    return json.loads(_canonical_bytes(value))


def _sorted_unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


@dataclass(frozen=True)
class ContextProvenance:
    source: str
    repository_commit: str = "unknown"
    observed_at: str | None = None
    parent_item_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("provenance source is required")
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "repository_commit", str(self.repository_commit or "unknown").strip() or "unknown")
        object.__setattr__(self, "parent_item_ids", _sorted_unique(self.parent_item_ids))
        object.__setattr__(self, "metadata", _json_clone(self.metadata))


@dataclass(frozen=True)
class ContextTrust:
    level: str = "unknown"
    confidence: float = 0.0
    taint: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level not in _TRUST_LEVELS:
            raise ValueError(f"unknown trust level: {self.level!r}")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("trust confidence must be in [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "taint", _sorted_unique(self.taint))
        object.__setattr__(self, "reasons", _sorted_unique(self.reasons))


@dataclass(frozen=True)
class ContextFreshness:
    state: str = "unknown"
    observed_at: str | None = None
    expires_at: str | None = None
    lease_id: str | None = None

    def __post_init__(self) -> None:
        if self.state not in _FRESHNESS_STATES:
            raise ValueError(f"unknown freshness state: {self.state!r}")
        if self.lease_id is not None and not str(self.lease_id).strip():
            raise ValueError("freshness lease_id may not be blank")


@dataclass(frozen=True)
class RecoveryHandle:
    kind: str
    locator: dict[str, Any]
    integrity: str
    exact: bool = True

    def __post_init__(self) -> None:
        if self.kind not in _RECOVERY_KINDS:
            raise ValueError(f"unknown recovery kind: {self.kind!r}")
        locator = _json_clone(self.locator)
        if not isinstance(locator, dict) or not locator:
            raise ValueError("recovery locator must be a non-empty object")
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "integrity", _normalize_digest(self.integrity))


@dataclass(frozen=True)
class UniversalContextItem:
    item_id: str
    kind: str
    representation: str
    content: Any
    content_sha256: str
    provenance: ContextProvenance
    trust: ContextTrust = field(default_factory=ContextTrust)
    freshness: ContextFreshness = field(default_factory=ContextFreshness)
    recovery: tuple[RecoveryHandle, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported UniversalContextItem schema: {self.schema_version}")
        if not self.kind.strip():
            raise ValueError("context item kind is required")
        if self.representation not in _REPRESENTATIONS:
            raise ValueError(f"unknown representation: {self.representation!r}")
        content = _json_clone(self.content)
        metadata = _json_clone(self.metadata)
        content_sha256 = _normalize_digest(self.content_sha256)
        item_id = _normalize_digest(self.item_id)
        object.__setattr__(self, "kind", self.kind.strip())
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "recovery", tuple(self.recovery))
        if _sha256(content) != content_sha256:
            raise ValueError("content_sha256 does not match canonical content")
        if self._computed_item_id() != item_id:
            raise ValueError("item_id does not match canonical identity payload")

    @classmethod
    def build(
        cls,
        *,
        kind: str,
        representation: str,
        content: Any,
        provenance: ContextProvenance,
        trust: ContextTrust | None = None,
        freshness: ContextFreshness | None = None,
        recovery: tuple[RecoveryHandle, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "UniversalContextItem":
        canonical_content = _json_clone(content)
        content_sha256 = _sha256(canonical_content)
        candidate = object.__new__(cls)
        object.__setattr__(candidate, "schema_version", _SCHEMA_VERSION)
        object.__setattr__(candidate, "item_id", "sha256:" + "0" * 64)
        object.__setattr__(candidate, "kind", str(kind).strip())
        object.__setattr__(candidate, "representation", representation)
        object.__setattr__(candidate, "content", canonical_content)
        object.__setattr__(candidate, "content_sha256", content_sha256)
        object.__setattr__(candidate, "provenance", provenance)
        object.__setattr__(candidate, "trust", trust or ContextTrust())
        object.__setattr__(candidate, "freshness", freshness or ContextFreshness())
        object.__setattr__(candidate, "recovery", tuple(recovery))
        object.__setattr__(candidate, "metadata", _json_clone(dict(metadata or {})))
        item_id = candidate._computed_item_id()
        return cls(
            item_id=item_id,
            kind=candidate.kind,
            representation=representation,
            content=canonical_content,
            content_sha256=content_sha256,
            provenance=provenance,
            trust=candidate.trust,
            freshness=candidate.freshness,
            recovery=tuple(recovery),
            metadata=candidate.metadata,
        )

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "representation": self.representation,
            "content_sha256": self.content_sha256,
            "provenance": {
                "source": self.provenance.source,
                "repository_commit": self.provenance.repository_commit,
                "observed_at": self.provenance.observed_at,
                "parent_item_ids": list(self.provenance.parent_item_ids),
                "metadata": self.provenance.metadata,
            },
            "recovery": [asdict(handle) for handle in self.recovery],
            "metadata": self.metadata,
        }

    def _computed_item_id(self) -> str:
        return _sha256(self._identity_payload())

    def verify_integrity(self) -> bool:
        return _sha256(self.content) == self.content_sha256 and self._computed_item_id() == self.item_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "item_id": self.item_id,
            "kind": self.kind,
            "representation": self.representation,
            "content": _json_clone(self.content),
            "content_sha256": self.content_sha256,
            "provenance": asdict(self.provenance),
            "trust": asdict(self.trust),
            "freshness": asdict(self.freshness),
            "recovery": [asdict(handle) for handle in self.recovery],
            "metadata": _json_clone(self.metadata),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UniversalContextItem":
        body = dict(value)
        return cls(
            schema_version=int(body.get("schema_version", -1)),
            item_id=str(body["item_id"]),
            kind=str(body["kind"]),
            representation=str(body["representation"]),
            content=body.get("content"),
            content_sha256=str(body["content_sha256"]),
            provenance=ContextProvenance(**dict(body["provenance"])),
            trust=ContextTrust(**dict(body.get("trust") or {})),
            freshness=ContextFreshness(**dict(body.get("freshness") or {})),
            recovery=tuple(RecoveryHandle(**dict(row)) for row in body.get("recovery", [])),
            metadata=dict(body.get("metadata") or {}),
        )

    @classmethod
    def from_context_pack_item(cls, item: Any, *, repository_commit: str = "unknown") -> "UniversalContextItem":
        content = {
            "text": str(item.text),
            "path": str(item.path),
            "start_line": int(item.start_line),
            "end_line": int(item.end_line),
        }
        file_hash = _normalize_digest(str(item.file_hash))
        recovery = RecoveryHandle(
            kind="file-range",
            locator={"path": str(item.path), "start_line": int(item.start_line), "end_line": int(item.end_line)},
            integrity=file_hash,
            exact=True,
        )
        return cls.build(
            kind=f"repository-{item.kind}",
            representation="exact",
            content=content,
            provenance=ContextProvenance(
                source="context-pack",
                repository_commit=repository_commit,
                metadata={"tier": str(item.tier), "reason": str(item.reason)},
            ),
            trust=ContextTrust(level="verified", confidence=1.0, reasons=("exact-file-range",)),
            freshness=ContextFreshness(state="fresh"),
            recovery=(recovery,),
            metadata={
                "tokens": int(item.tokens),
                "token_confidence": str(item.token_confidence),
                "file_hash": file_hash,
            },
        )

    @classmethod
    def from_evidence_node(cls, node: Any) -> "UniversalContextItem":
        content = {"node_id": str(node.node_id), "label": str(node.label)}
        node_integrity = _sha256(content)
        return cls.build(
            kind=f"evidence-node:{node.kind}",
            representation="structural",
            content=content,
            provenance=ContextProvenance(
                source=str(node.source),
                repository_commit=str(node.repository_commit),
                metadata=dict(node.metadata),
            ),
            trust=ContextTrust(level="observed", confidence=float(node.confidence), reasons=("runtime-evidence-node",)),
            recovery=(RecoveryHandle(kind="evidence-node", locator={"node_id": str(node.node_id)}, integrity=node_integrity, exact=True),),
            metadata={"evidence_kind": str(node.kind)},
        )

    @classmethod
    def from_evidence_edge(cls, edge: Any) -> "UniversalContextItem":
        content = {
            "source": str(edge.source),
            "target": str(edge.target),
            "relation": str(edge.relation),
            "evidence": str(edge.evidence),
        }
        evidence_digest = _normalize_digest(str(edge.evidence))
        return cls.build(
            kind="evidence-edge",
            representation="structural",
            content=content,
            provenance=ContextProvenance(
                source="runtime-evidence-edge",
                repository_commit=str(edge.repository_commit),
                observed_at=str(edge.observed_at),
                metadata=dict(edge.metadata),
            ),
            trust=ContextTrust(level="observed", confidence=float(edge.confidence), reasons=("runtime-evidence-edge",)),
            freshness=ContextFreshness(state="fresh", observed_at=str(edge.observed_at)),
            recovery=(RecoveryHandle(kind="evidence-edge", locator={"evidence": str(edge.evidence)}, integrity=evidence_digest, exact=True),),
            metadata={"relation": str(edge.relation)},
        )


__all__ = [
    "ContextFreshness",
    "ContextProvenance",
    "ContextTrust",
    "RecoveryHandle",
    "UniversalContextItem",
]
