from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .evidence_store import EvidenceStoreV2
from .universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)

_SCHEMA_VERSION = 1
_REPRESENTATIONS = {"exact", "structural", "semantic", "bounded-preview"}

TYPE_SPECS: dict[str, tuple[str, ...]] = {
    "GitDiff": ("base", "head", "files", "patch"),
    "TestRun": ("command", "exit_code", "tests"),
    "CompilerDiagnostics": ("tool", "diagnostics"),
    "ASTGraph": ("language", "nodes", "edges"),
    "DependencyGraph": ("nodes", "edges"),
    "SearchResultSet": ("query", "results"),
    "LogStream": ("source", "entries"),
    "BrowserDOM": ("url", "nodes"),
    "TraceSet": ("traces",),
    "MetricSeries": ("name", "points"),
    "DataFrame": ("columns", "rows"),
    "FileSnapshot": ("path", "content"),
    "SymbolSnapshot": ("symbol", "kind", "path"),
    "ToolSchemaSet": ("tools",),
    "MemoryObservation": ("observation", "scope"),
    "TaskStateSnapshot": ("task_id", "state"),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _clone(value: Any) -> Any:
    return json.loads(_canonical(value))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _validate_payload(object_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if object_type not in TYPE_SPECS:
        raise ValueError(f"unknown typed context object: {object_type!r}")
    body = _clone(dict(payload))
    if not isinstance(body, dict):
        raise ValueError("typed context payload must be an object")
    missing = [name for name in TYPE_SPECS[object_type] if name not in body]
    if missing:
        raise ValueError(f"{object_type} missing required payload fields: {missing}")
    return body


@dataclass(frozen=True)
class TypedContextObject:
    object_type: str
    representation: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported typed context schema: {self.schema_version}")
        if self.representation not in _REPRESENTATIONS:
            raise ValueError(f"unknown representation: {self.representation!r}")
        object.__setattr__(self, "payload", _validate_payload(self.object_type, self.payload))
        object.__setattr__(self, "metadata", _clone(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "object_type": self.object_type,
            "representation": self.representation,
            "payload": _clone(self.payload),
            "metadata": _clone(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TypedContextObject":
        body = dict(value)
        return cls(
            schema_version=int(body.get("schema_version", -1)),
            object_type=str(body["object_type"]),
            representation=str(body["representation"]),
            payload=dict(body["payload"]),
            metadata=dict(body.get("metadata") or {}),
        )

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_dict())

    @property
    def object_sha256(self) -> str:
        return _digest(self.to_dict())

    def to_universal(
        self,
        *,
        provenance: ContextProvenance,
        trust: ContextTrust | None = None,
        freshness: ContextFreshness | None = None,
        recovery: tuple[RecoveryHandle, ...] = (),
    ) -> UniversalContextItem:
        return UniversalContextItem.build(
            kind=f"typed-context:{self.object_type}",
            representation=self.representation,
            content=self.to_dict(),
            provenance=provenance,
            trust=trust or ContextTrust(),
            freshness=freshness or ContextFreshness(),
            recovery=recovery,
            metadata={
                "typed_context_schema_version": self.schema_version,
                "typed_object_type": self.object_type,
                "typed_object_sha256": self.object_sha256,
            },
        )

    @classmethod
    def from_universal(cls, item: UniversalContextItem) -> "TypedContextObject":
        if not item.verify_integrity():
            raise ValueError("UniversalContextItem integrity check failed")
        prefix = "typed-context:"
        if not item.kind.startswith(prefix):
            raise ValueError(f"not a typed context item: {item.kind!r}")
        value = cls.from_dict(item.content)
        expected_type = item.kind[len(prefix):]
        if value.object_type != expected_type:
            raise ValueError("typed context kind/object_type mismatch")
        if value.representation != item.representation:
            raise ValueError("typed context representation mismatch")
        expected_digest = item.metadata.get("typed_object_sha256")
        if expected_digest != value.object_sha256:
            raise ValueError("typed context object digest mismatch")
        if item.metadata.get("typed_context_schema_version") != value.schema_version:
            raise ValueError("typed context schema metadata mismatch")
        return value


class TypedContextObjectStore:
    """Typed codec facade over EvidenceStoreV2; no parallel persistence engine."""

    def __init__(self, path: Path):
        self.evidence = EvidenceStoreV2(path)

    def put(
        self,
        value: TypedContextObject,
        *,
        provenance: ContextProvenance,
        trust: ContextTrust | None = None,
        freshness: ContextFreshness | None = None,
        recovery: tuple[RecoveryHandle, ...] = (),
        expires_at: str | None = None,
        pinned: bool = False,
        actor: str = "typed-context-object-store-v1",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        item = value.to_universal(
            provenance=provenance,
            trust=trust,
            freshness=freshness,
            recovery=recovery,
        )
        receipt = self.evidence.put(
            item,
            expires_at=expires_at,
            pinned=pinned,
            actor=actor,
            observed_at=observed_at,
        )
        return {
            **receipt,
            "object_type": value.object_type,
            "representation": value.representation,
            "object_sha256": value.object_sha256,
        }

    def get(self, item_id: str) -> TypedContextObject | None:
        item = self.evidence.get(item_id)
        if item is None:
            return None
        return TypedContextObject.from_universal(item)

    def require(self, item_id: str) -> TypedContextObject:
        value = self.get(item_id)
        if value is None:
            raise KeyError(item_id)
        return value

    def get_universal(self, item_id: str) -> UniversalContextItem | None:
        return self.evidence.get(item_id)

    def lineage(self, item_id: str, *, direction: str = "parents") -> list[dict[str, Any]]:
        return self.evidence.lineage(item_id, direction=direction)

    def verify_item(self, item_id: str) -> dict[str, Any]:
        return self.evidence.verify_item(item_id)

    def stats(self) -> dict[str, Any]:
        return self.evidence.stats()


__all__ = ["TYPE_SPECS", "TypedContextObject", "TypedContextObjectStore"]
