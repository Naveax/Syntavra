from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit

from .universal_context_item import UniversalContextItem
from .util import canonical_json, sha256_bytes


SCHEME = "syntavra"
LEVELS = ("L0", "L1", "L2", "L3")
ROOTS = ("repo", "evidence", "memory", "task", "tool", "artifact", "context")
_MAX_REASON_CHARS = 512
_MAX_LABEL_CHARS = 160
_MAX_CHILDREN = 256
_MAX_STRUCTURE_DEPTH = 5
_MAX_STRUCTURE_KEYS = 64

ContextItemResolver = Callable[[str], UniversalContextItem | None]


def _hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _bounded_text(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _validate_level(level: str) -> str:
    normalized = str(level).upper()
    if normalized not in LEVELS:
        raise ValueError(f"unknown context disclosure level: {level!r}")
    return normalized


def _structure(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth >= _MAX_STRUCTURE_DEPTH:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, Mapping):
        keys = sorted(str(key) for key in value)
        selected = keys[:_MAX_STRUCTURE_KEYS]
        return {
            "type": "object",
            "key_count": len(keys),
            "keys": selected,
            "fields": {key: _structure(value[key], depth=depth + 1) for key in selected if key in value},
            "truncated": len(keys) > len(selected),
        }
    if isinstance(value, list):
        preview = [_structure(row, depth=depth + 1) for row in value[:8]]
        return {
            "type": "array",
            "length": len(value),
            "items": preview,
            "truncated": len(value) > len(preview),
        }
    if isinstance(value, tuple):
        return _structure(list(value), depth=depth)
    if isinstance(value, str):
        return {
            "type": "string",
            "chars": len(value),
            "lines": value.count("\n") + 1 if value else 0,
            "sha256": _hash(value),
        }
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    return {"type": type(value).__name__}


@dataclass(frozen=True)
class ContextNamespaceAddress:
    root: str
    segments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root = str(self.root).strip().casefold()
        if root not in ROOTS:
            raise ValueError(f"unknown syntavra namespace root: {self.root!r}")
        normalized: list[str] = []
        for raw in self.segments:
            segment = str(raw).strip()
            if not segment or segment in {".", ".."}:
                raise ValueError("namespace segments must be non-empty and traversal-free")
            if "/" in segment or "\\" in segment or "\x00" in segment:
                raise ValueError(f"namespace segment is not canonical: {segment!r}")
            normalized.append(segment)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "segments", tuple(normalized))

    @property
    def uri(self) -> str:
        suffix = "/".join(quote(part, safe="-._~") for part in self.segments)
        return f"{SCHEME}://{self.root}" + (f"/{suffix}" if suffix else "")

    @classmethod
    def parse(cls, uri: str) -> "ContextNamespaceAddress":
        parsed = urlsplit(str(uri))
        if parsed.scheme != SCHEME:
            raise ValueError(f"context namespace must use {SCHEME}://")
        if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
            raise ValueError("context namespace URI cannot contain query, fragment, credentials, or port")
        root = parsed.hostname or parsed.netloc
        raw_segments = tuple(part for part in parsed.path.split("/") if part)
        decoded = tuple(unquote(part) for part in raw_segments)
        candidate = cls(root=root, segments=decoded)
        if candidate.uri != str(uri).rstrip("/"):
            raise ValueError("context namespace URI is not canonical")
        return candidate

    @classmethod
    def repository(
        cls,
        repository: str,
        *,
        directory: str | None = None,
        file: str | None = None,
        symbol: str | None = None,
        lines: tuple[int, int] | None = None,
    ) -> "ContextNamespaceAddress":
        repository_id = str(repository).strip()
        if not repository_id:
            raise ValueError("repository namespace requires repository identity")
        segments: list[str] = [repository_id]
        directory_parts: list[str] = []
        if directory:
            directory_parts = [part for part in str(directory).replace("\\", "/").split("/") if part]
            if any(part in {".", ".."} for part in directory_parts):
                raise ValueError("repository directory cannot traverse parents")
            segments.extend(["dir", *directory_parts])
        if file:
            file_parts = [part for part in str(file).replace("\\", "/").split("/") if part]
            if any(part in {".", ".."} for part in file_parts):
                raise ValueError("repository file cannot traverse parents")
            if directory_parts and file_parts[: len(directory_parts)] == directory_parts:
                file_parts = file_parts[len(directory_parts):]
            if not file_parts:
                raise ValueError("repository file path cannot be empty")
            segments.extend(["file", *file_parts])
        elif symbol is not None or lines is not None:
            raise ValueError("symbol/lines require a repository file")
        if symbol is not None:
            normalized_symbol = str(symbol).strip()
            if not normalized_symbol:
                raise ValueError("repository symbol cannot be empty")
            segments.extend(["symbol", normalized_symbol])
        if lines is not None:
            if symbol is None:
                raise ValueError("line range requires a symbol address")
            start, end = int(lines[0]), int(lines[1])
            if start < 1 or end < start:
                raise ValueError("invalid repository line range")
            segments.extend(["lines", f"{start}-{end}"])
        return cls(root="repo", segments=tuple(segments))


@dataclass(frozen=True)
class ContextNamespaceEntry:
    uri: str
    item_id: str
    label: str
    reason: str
    parent_uri: str | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalStep:
    sequence: int
    operation: str
    uri: str
    level: str
    result_hash: str
    reason: str
    receipt_hash: str


@dataclass
class _Trajectory:
    trajectory_id: str
    query: str
    root_uri: str | None
    steps: list[RetrievalStep] = field(default_factory=list)


@dataclass(frozen=True)
class _Binding:
    entry: ContextNamespaceEntry
    resolver: ContextItemResolver


class ContextNamespace:
    """Resolver-backed syntavra:// browser with progressive disclosure.

    This class stores only namespace bindings and retrieval trajectory state. It
    does not persist or copy canonical context payloads. The UniversalContextItem
    returned by each resolver remains the content authority and is integrity-
    checked on every browse/why/reveal operation.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, _Binding] = {}
        self._children: dict[str, set[str]] = {}
        self._trajectories: dict[str, _Trajectory] = {}

    @staticmethod
    def canonical_uri(uri: str) -> str:
        return ContextNamespaceAddress.parse(uri).uri

    def register(
        self,
        *,
        uri: str,
        item_id: str,
        resolver: ContextItemResolver,
        label: str,
        reason: str,
        parent_uri: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextNamespaceEntry:
        canonical = self.canonical_uri(uri)
        if canonical in self._bindings:
            raise ValueError(f"context namespace URI already registered: {canonical}")
        normalized_parent = self.canonical_uri(parent_uri) if parent_uri else None
        if normalized_parent is not None and normalized_parent not in self._bindings:
            raise KeyError(f"context namespace parent is not registered: {normalized_parent}")
        normalized_label = _bounded_text(label, _MAX_LABEL_CHARS)
        normalized_reason = _bounded_text(reason, _MAX_REASON_CHARS)
        if not normalized_label:
            raise ValueError("context namespace label is required")
        if not normalized_reason:
            raise ValueError("context namespace reason is required")
        item = resolver(str(item_id))
        if item is None:
            raise KeyError(f"context namespace resolver cannot resolve item: {item_id}")
        if item.item_id != str(item_id):
            raise ValueError("context namespace resolver returned a different item identity")
        if not item.verify_integrity():
            raise ValueError("context namespace item failed UniversalContextItem integrity")
        entry = ContextNamespaceEntry(
            uri=canonical,
            item_id=item.item_id,
            label=normalized_label,
            reason=normalized_reason,
            parent_uri=normalized_parent,
            tags=tuple(sorted({str(tag).strip() for tag in tags if str(tag).strip()})),
            metadata=dict(metadata or {}),
        )
        self._bindings[canonical] = _Binding(entry=entry, resolver=resolver)
        if normalized_parent is not None:
            self._children.setdefault(normalized_parent, set()).add(canonical)
        self._children.setdefault(canonical, set())
        return entry

    def bind_item(
        self,
        uri: str,
        item: UniversalContextItem,
        *,
        label: str,
        reason: str,
        parent_uri: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextNamespaceEntry:
        def resolver(item_id: str) -> UniversalContextItem | None:
            return item if item_id == item.item_id else None

        return self.register(
            uri=uri,
            item_id=item.item_id,
            resolver=resolver,
            label=label,
            reason=reason,
            parent_uri=parent_uri,
            tags=tags,
            metadata=metadata,
        )

    def bind_resolver(
        self,
        uri: str,
        item_id: str,
        resolver: ContextItemResolver,
        *,
        label: str,
        reason: str,
        parent_uri: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextNamespaceEntry:
        return self.register(
            uri=uri,
            item_id=item_id,
            resolver=resolver,
            label=label,
            reason=reason,
            parent_uri=parent_uri,
            tags=tags,
            metadata=metadata,
        )

    def _binding(self, uri: str) -> _Binding:
        canonical = self.canonical_uri(uri)
        binding = self._bindings.get(canonical)
        if binding is None:
            raise KeyError(canonical)
        return binding

    def _resolve(self, binding: _Binding) -> UniversalContextItem:
        item = binding.resolver(binding.entry.item_id)
        if item is None:
            raise KeyError(f"context item no longer resolves: {binding.entry.item_id}")
        if item.item_id != binding.entry.item_id:
            raise ValueError("context resolver identity drift")
        if not item.verify_integrity():
            raise ValueError("context item integrity drift")
        return item

    def _view(self, binding: _Binding, level: str) -> dict[str, Any]:
        level = _validate_level(level)
        entry = binding.entry
        item = self._resolve(binding)
        child_count = len(self._children.get(entry.uri, ()))
        result: dict[str, Any] = {
            "level": level,
            "uri": entry.uri,
            "item_id": item.item_id,
            "label": entry.label,
            "kind": item.kind,
            "representation": item.representation,
            "content_sha256": item.content_sha256,
            "parent_uri": entry.parent_uri,
            "child_count": child_count,
            "tags": list(entry.tags),
        }
        if level in {"L1", "L2", "L3"}:
            result.update({
                "reason": entry.reason,
                "provenance": {
                    "source": item.provenance.source,
                    "repository_commit": item.provenance.repository_commit,
                    "parent_item_ids": list(item.provenance.parent_item_ids),
                },
                "trust": {
                    "level": item.trust.level,
                    "confidence": item.trust.confidence,
                    "taint": list(item.trust.taint),
                    "reasons": list(item.trust.reasons),
                },
                "freshness": {
                    "state": item.freshness.state,
                    "observed_at": item.freshness.observed_at,
                    "expires_at": item.freshness.expires_at,
                    "lease_id": item.freshness.lease_id,
                },
                "exact_recovery_available": any(handle.exact for handle in item.recovery),
                "recovery_kinds": sorted({handle.kind for handle in item.recovery}),
            })
        if level in {"L2", "L3"}:
            result.update({
                "structure": _structure(item.content),
                "metadata_keys": sorted(str(key) for key in item.metadata),
                "namespace_metadata_keys": sorted(str(key) for key in entry.metadata),
            })
        if level == "L3":
            result.update({
                "content": item.content,
                "metadata": item.metadata,
                "namespace_metadata": entry.metadata,
                "provenance_exact": asdict(item.provenance),
                "recovery": [asdict(handle) for handle in item.recovery],
            })
        result["view_hash"] = _hash(result)
        return result

    def start_trajectory(self, query: str, *, root_uri: str | None = None) -> str:
        normalized_query = _bounded_text(query, 1_024)
        if not normalized_query:
            raise ValueError("retrieval trajectory query is required")
        normalized_root = self.canonical_uri(root_uri) if root_uri else None
        if normalized_root is not None and normalized_root not in self._bindings:
            raise KeyError(normalized_root)
        trajectory_id = _hash({"query": normalized_query, "root_uri": normalized_root})
        if trajectory_id in self._trajectories:
            raise ValueError("retrieval trajectory already exists for this query/root")
        self._trajectories[trajectory_id] = _Trajectory(
            trajectory_id=trajectory_id,
            query=normalized_query,
            root_uri=normalized_root,
        )
        return trajectory_id

    def _operation_receipt(
        self,
        *,
        operation: str,
        uri: str,
        level: str,
        result_hash: str,
        reason: str,
        trajectory_id: str | None,
    ) -> dict[str, Any]:
        sequence: int | None = None
        if trajectory_id is not None:
            trajectory = self._trajectories.get(str(trajectory_id))
            if trajectory is None:
                raise KeyError(f"unknown retrieval trajectory: {trajectory_id}")
            sequence = len(trajectory.steps) + 1
        base = {
            "operation": operation,
            "uri": uri,
            "level": level,
            "result_hash": result_hash,
            "reason": _bounded_text(reason, _MAX_REASON_CHARS),
            "trajectory_id": trajectory_id,
            "sequence": sequence,
        }
        receipt_hash = _hash(base)
        if trajectory_id is not None:
            trajectory = self._trajectories[str(trajectory_id)]
            trajectory.steps.append(RetrievalStep(
                sequence=int(sequence),
                operation=operation,
                uri=uri,
                level=level,
                result_hash=result_hash,
                reason=base["reason"],
                receipt_hash=receipt_hash,
            ))
        return {**base, "receipt_hash": receipt_hash}

    def reveal(
        self,
        uri: str,
        *,
        level: str = "L3",
        trajectory_id: str | None = None,
    ) -> dict[str, Any]:
        binding = self._binding(uri)
        view = self._view(binding, level)
        receipt = self._operation_receipt(
            operation="reveal",
            uri=binding.entry.uri,
            level=view["level"],
            result_hash=view["view_hash"],
            reason=binding.entry.reason,
            trajectory_id=trajectory_id,
        )
        return {"view": view, "receipt": receipt}

    def why(self, uri: str, *, trajectory_id: str | None = None) -> dict[str, Any]:
        binding = self._binding(uri)
        item = self._resolve(binding)
        explanation = {
            "uri": binding.entry.uri,
            "item_id": item.item_id,
            "reason": binding.entry.reason,
            "parent_uri": binding.entry.parent_uri,
            "provenance_source": item.provenance.source,
            "repository_commit": item.provenance.repository_commit,
            "parent_item_ids": list(item.provenance.parent_item_ids),
            "trust_level": item.trust.level,
            "taint": list(item.trust.taint),
            "freshness": item.freshness.state,
            "exact_recovery_available": any(handle.exact for handle in item.recovery),
            "tags": list(binding.entry.tags),
        }
        explanation_hash = _hash(explanation)
        receipt = self._operation_receipt(
            operation="why",
            uri=binding.entry.uri,
            level="L1",
            result_hash=explanation_hash,
            reason=binding.entry.reason,
            trajectory_id=trajectory_id,
        )
        return {"explanation": {**explanation, "explanation_hash": explanation_hash}, "receipt": receipt}

    def browse(
        self,
        uri: str,
        *,
        level: str = "L0",
        limit: int = 64,
        trajectory_id: str | None = None,
    ) -> dict[str, Any]:
        if limit < 1 or limit > _MAX_CHILDREN:
            raise ValueError(f"context browser limit must be in [1, {_MAX_CHILDREN}]")
        binding = self._binding(uri)
        normalized_level = _validate_level(level)
        child_uris = sorted(self._children.get(binding.entry.uri, ()))
        selected = child_uris[: int(limit)]
        node = self._view(binding, normalized_level)
        children = [self._view(self._bindings[child_uri], normalized_level) for child_uri in selected]
        result = {
            "uri": binding.entry.uri,
            "level": normalized_level,
            "node": node,
            "children": children,
            "child_count": len(child_uris),
            "truncated": len(child_uris) > len(selected),
        }
        result_hash = _hash(result)
        receipt = self._operation_receipt(
            operation="browse",
            uri=binding.entry.uri,
            level=normalized_level,
            result_hash=result_hash,
            reason="progressive context namespace browse",
            trajectory_id=trajectory_id,
        )
        return {**result, "result_hash": result_hash, "receipt": receipt}

    def trajectory_receipt(self, trajectory_id: str) -> dict[str, Any]:
        trajectory = self._trajectories.get(str(trajectory_id))
        if trajectory is None:
            raise KeyError(f"unknown retrieval trajectory: {trajectory_id}")
        body = {
            "trajectory_id": trajectory.trajectory_id,
            "query": trajectory.query,
            "root_uri": trajectory.root_uri,
            "steps": [asdict(step) for step in trajectory.steps],
        }
        return {**body, "trajectory_hash": _hash(body)}

    def status(self) -> dict[str, Any]:
        return {
            "scheme": SCHEME,
            "roots": list(ROOTS),
            "levels": list(LEVELS),
            "entries": len(self._bindings),
            "trajectories": len(self._trajectories),
            "persistent_store": False,
        }


__all__ = [
    "ContextNamespace",
    "ContextNamespaceAddress",
    "ContextNamespaceEntry",
    "LEVELS",
    "ROOTS",
    "RetrievalStep",
    "SCHEME",
]
