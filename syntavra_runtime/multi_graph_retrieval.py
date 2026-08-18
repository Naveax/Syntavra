from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .context_namespace import ContextNamespaceAddress


GRAPH_KINDS = frozenset({
    "code",
    "semantic",
    "temporal",
    "causal",
    "entity",
    "task",
    "provenance",
    "security",
})
_FORBIDDEN_METADATA_KEYS = frozenset({"content", "payload", "raw_text", "body", "secret"})
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:@-]+", re.UNICODE)
_GRAPH_WEIGHT = {
    "task": 1.12,
    "code": 1.00,
    "semantic": 0.98,
    "causal": 0.96,
    "entity": 0.92,
    "provenance": 0.90,
    "temporal": 0.88,
    "security": 0.86,
}
_TRUST_WEIGHT = {
    "verified": 1.00,
    "trusted": 0.96,
    "unknown": 0.78,
    "untrusted": 0.42,
    "denied": 0.0,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.casefold() for token in _TOKEN_RE.findall(value) if len(token) > 1))


def _metadata_is_safe(value: Any, *, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_METADATA_KEYS:
                raise ValueError(f"{path} cannot carry payload authority: {key}")
            _metadata_is_safe(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _metadata_is_safe(nested, path=f"{path}[{index}]")


@dataclass(frozen=True)
class GraphNode:
    graph_kind: str
    node_id: str
    label: str
    node_type: str = "entity"
    namespace_uri: str = ""
    item_id: str = ""
    evidence_refs: tuple[str, ...] = ()
    trust_level: str = "unknown"
    tainted: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.graph_kind not in GRAPH_KINDS:
            raise ValueError(f"unsupported graph kind: {self.graph_kind}")
        if not self.node_id.strip() or not self.label.strip():
            raise ValueError("graph nodes require stable node_id and label")
        if self.trust_level not in _TRUST_WEIGHT:
            raise ValueError(f"unsupported trust level: {self.trust_level}")
        _metadata_is_safe(self.metadata)
        if self.namespace_uri:
            parsed = ContextNamespaceAddress.parse(self.namespace_uri)
            if parsed.uri != self.namespace_uri:
                raise ValueError("namespace_uri must be canonical")
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(str(value) for value in self.evidence_refs if str(value)))))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def identity(self) -> str:
        if self.item_id:
            return f"item:{self.item_id}"
        if self.namespace_uri:
            return f"uri:{self.namespace_uri}"
        return f"node:{self.graph_kind}:{self.node_id}"

    def searchable_text(self) -> str:
        safe_metadata = _canonical(self.metadata)
        return f"{self.label} {self.node_type} {self.namespace_uri} {self.item_id} {safe_metadata}".casefold()


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    confidence: float = 1.0
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source or not self.target or not self.relation:
            raise ValueError("graph edges require source, target and relation")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("edge confidence must be within [0,1]")
        _metadata_is_safe(self.metadata)
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(str(value) for value in self.evidence_refs if str(value)))))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class GraphLayer:
    name: str
    graph_kind: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...] = ()
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("graph layer name is required")
        if self.graph_kind not in GRAPH_KINDS:
            raise ValueError(f"unsupported graph kind: {self.graph_kind}")
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate node ids in graph layer {self.name}")
        if any(node.graph_kind != self.graph_kind for node in self.nodes):
            raise ValueError("node graph_kind must match its layer")
        known = set(ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"edge endpoint missing from layer {self.name}")
        object.__setattr__(self, "source_refs", tuple(sorted(set(str(value) for value in self.source_refs if str(value)))))


class MultiGraphRetrieval:
    """Deterministic task-aware fusion over existing graph evidence.

    The engine is intentionally not a database and never owns exact payloads.
    Layers carry references, identities and bounded metadata only. Existing
    repository, memory, task, provenance and security systems remain the source
    authorities; this object composes their graph views for retrieval.
    """

    def __init__(self) -> None:
        self._layers: dict[str, GraphLayer] = {}

    def add_layer(
        self,
        name: str,
        graph_kind: str,
        nodes: Iterable[GraphNode],
        edges: Iterable[GraphEdge] = (),
        *,
        source_refs: Iterable[str] = (),
    ) -> GraphLayer:
        if name in self._layers:
            raise ValueError(f"graph layer already registered: {name}")
        layer = GraphLayer(
            name=name,
            graph_kind=graph_kind,
            nodes=tuple(nodes),
            edges=tuple(edges),
            source_refs=tuple(source_refs),
        )
        self._layers[name] = layer
        return layer

    def add_structural_snapshot(
        self,
        name: str,
        snapshot: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        repository_id: str,
        graph_kind: str = "code",
        source_refs: Iterable[str] = ("syntavra_runtime/structural.py:StructuralIndex",),
    ) -> GraphLayer:
        if graph_kind not in {"code", "semantic"}:
            raise ValueError("structural snapshots may only populate code/semantic graph views")
        symbols = list(snapshot.get("symbols") or ())
        nodes: list[GraphNode] = []
        key_by_pair: dict[tuple[str, str], str] = {}
        short_by_path: dict[tuple[str, str], str] = {}
        for row in symbols:
            path = str(row.get("path") or "")
            name_value = str(row.get("name") or "")
            qualified = str(row.get("qualified_name") or name_value)
            if not path or not qualified:
                continue
            node_id = f"{path}:{qualified}:{int(row.get('line') or 1)}"
            directory = Path(path).parent.as_posix()
            uri = ContextNamespaceAddress.repository(
                repository_id,
                directory=None if directory == "." else directory,
                file=path,
                symbol=qualified,
            ).uri
            confidence = float(row.get("confidence") or 0.0)
            trust = "verified" if confidence >= 0.9 else "trusted" if confidence >= 0.7 else "unknown"
            parser = str(row.get("parser") or "")
            nodes.append(GraphNode(
                graph_kind=graph_kind,
                node_id=node_id,
                label=qualified,
                node_type=str(row.get("kind") or "symbol"),
                namespace_uri=uri,
                evidence_refs=tuple(filter(None, (f"repository:{path}", f"parser:{parser}" if parser else ""))),
                trust_level=trust,
                metadata={
                    "path": path,
                    "line": int(row.get("line") or 1),
                    "end_line": int(row.get("end_line") or row.get("line") or 1),
                    "confidence": confidence,
                    "parser": parser,
                },
            ))
            key_by_pair[(path, qualified)] = node_id
            key_by_pair[(path, name_value)] = node_id
            short_by_path[(path, qualified.replace("::", ".").rsplit(".", 1)[-1])] = node_id

        edges: list[GraphEdge] = []
        for row in snapshot.get("edges") or ():
            source_path = str(row.get("source_path") or "")
            source_symbol = str(row.get("source_symbol") or "")
            target_path = str(row.get("target_path") or "")
            target = str(row.get("target") or "")
            source_id = key_by_pair.get((source_path, source_symbol)) or short_by_path.get((source_path, source_symbol.replace("::", ".").rsplit(".", 1)[-1]))
            target_short = target.replace("::", ".").rsplit(".", 1)[-1]
            target_id = key_by_pair.get((target_path, target)) or short_by_path.get((target_path, target_short)) if target_path else None
            if not source_id or not target_id or source_id == target_id:
                continue
            edges.append(GraphEdge(
                source=source_id,
                target=target_id,
                relation=str(row.get("edge_type") or "related"),
                confidence=max(0.0, min(1.0, float(row.get("confidence") or 0.0))),
                evidence_refs=(f"edge:{source_path}:{int(row.get('line') or 0)}",),
                metadata={"target_path": target_path},
            ))
        return self.add_layer(name, graph_kind, nodes, edges, source_refs=source_refs)

    def _blocked_identities(self) -> set[str]:
        blocked: set[str] = set()
        for layer in self._layers.values():
            for node in layer.nodes:
                if node.tainted:
                    blocked.add(node.identity)
                if node.graph_kind == "security" and str(node.metadata.get("disposition") or "").casefold() in {"deny", "block", "quarantine"}:
                    blocked.add(node.identity)
        return blocked

    @staticmethod
    def _freshness_weight(node: GraphNode) -> float:
        value = str(node.metadata.get("freshness") or "current").casefold()
        return {"current": 1.0, "fresh": 1.0, "aging": 0.82, "stale": 0.55, "expired": 0.0}.get(value, 0.9)

    @staticmethod
    def _lexical_score(task: str, task_tokens: set[str], node: GraphNode) -> tuple[float, list[str]]:
        corpus = node.searchable_text()
        corpus_tokens = set(_tokens(corpus))
        matched = sorted(task_tokens & corpus_tokens)
        overlap = len(matched) / max(1, len(task_tokens))
        phrase = 1.0 if task.casefold().strip() and task.casefold().strip() in corpus else 0.0
        score = overlap * 10.0 + phrase * 5.0
        if node.graph_kind == "task" and matched:
            score += 2.0
        return score, matched

    def retrieve(
        self,
        task: str,
        *,
        limit: int = 20,
        max_hops: int = 2,
        required_graphs: Iterable[str] = (),
        include_tainted: bool = False,
    ) -> dict[str, Any]:
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("task query is required")
        if not self._layers:
            raise RuntimeError("no graph layers are registered")
        limit = max(1, min(int(limit), 100))
        max_hops = max(0, min(int(max_hops), 4))
        available_graphs = {layer.graph_kind for layer in self._layers.values()}
        required = {str(value) for value in required_graphs}
        unknown_required = required - GRAPH_KINDS
        if unknown_required:
            raise ValueError(f"unknown required graph kinds: {sorted(unknown_required)}")
        missing = required - available_graphs
        if missing:
            raise RuntimeError(f"required graph layers unavailable: {sorted(missing)}")

        blocked = set() if include_tainted else self._blocked_identities()
        task_tokens = set(_tokens(normalized_task))
        node_by_key: dict[str, GraphNode] = {}
        layer_by_key: dict[str, GraphLayer] = {}
        lexical_by_key: dict[str, float] = {}
        matched_by_key: dict[str, list[str]] = {}
        adjacency: dict[str, list[tuple[str, float, str, tuple[str, ...]]]] = defaultdict(list)

        for layer_name in sorted(self._layers):
            layer = self._layers[layer_name]
            local_key: dict[str, str] = {}
            for node in layer.nodes:
                key = f"{layer_name}:{node.node_id}"
                local_key[node.node_id] = key
                node_by_key[key] = node
                layer_by_key[key] = layer
                lexical, matched = self._lexical_score(normalized_task, task_tokens, node)
                trust = _TRUST_WEIGHT[node.trust_level]
                freshness = self._freshness_weight(node)
                lexical_by_key[key] = lexical * _GRAPH_WEIGHT[node.graph_kind] * trust * freshness
                matched_by_key[key] = matched
            for edge in layer.edges:
                source = local_key[edge.source]
                target = local_key[edge.target]
                refs = tuple(sorted(set((*edge.evidence_refs, *layer.source_refs))))
                adjacency[source].append((target, float(edge.confidence), edge.relation, refs))
                adjacency[target].append((source, float(edge.confidence) * 0.72, f"reverse:{edge.relation}", refs))

        scores = dict(lexical_by_key)
        reasons: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seeds = sorted(
            (key for key, score in lexical_by_key.items() if score > 0.0),
            key=lambda key: (-lexical_by_key[key], key),
        )[: max(limit * 4, 16)]
        queue = deque((key, 0, lexical_by_key[key]) for key in seeds)
        best_depth: dict[tuple[str, str], int] = {}
        for key in seeds:
            if matched_by_key[key]:
                reasons[key].append({"kind": "lexical", "matched_terms": matched_by_key[key]})
        while queue:
            source, depth, source_score = queue.popleft()
            if depth >= max_hops:
                continue
            for target, confidence, relation, refs in sorted(adjacency.get(source, ()), key=lambda row: (row[0], row[2])):
                next_depth = depth + 1
                pair = (source, target)
                previous_depth = best_depth.get(pair)
                if previous_depth is not None and previous_depth <= next_depth:
                    continue
                best_depth[pair] = next_depth
                propagated = source_score * confidence * (0.42 ** next_depth)
                if propagated <= 0.0:
                    continue
                scores[target] = scores.get(target, 0.0) + propagated
                reasons[target].append({
                    "kind": "graph",
                    "relation": relation,
                    "depth": next_depth,
                    "evidence_refs": list(refs),
                })
                queue.append((target, next_depth, propagated))

        fused: dict[str, dict[str, Any]] = {}
        for key, score in scores.items():
            if score <= 0.0:
                continue
            node = node_by_key[key]
            if node.identity in blocked:
                continue
            layer = layer_by_key[key]
            candidate = fused.setdefault(node.identity, {
                "identity": node.identity,
                "namespace_uri": node.namespace_uri,
                "item_id": node.item_id,
                "labels": set(),
                "node_types": set(),
                "graph_kinds": set(),
                "layers": set(),
                "evidence_refs": set(),
                "score": 0.0,
                "reasons": [],
                "trust_levels": set(),
            })
            candidate["labels"].add(node.label)
            candidate["node_types"].add(node.node_type)
            candidate["graph_kinds"].add(node.graph_kind)
            candidate["layers"].add(layer.name)
            candidate["evidence_refs"].update(node.evidence_refs)
            candidate["evidence_refs"].update(layer.source_refs)
            candidate["score"] += score
            candidate["reasons"].extend(reasons.get(key, ()))
            candidate["trust_levels"].add(node.trust_level)

        rows: list[dict[str, Any]] = []
        for candidate in fused.values():
            graph_count = len(candidate["graph_kinds"])
            candidate["score"] += max(0, graph_count - 1) * 1.25
            rows.append({
                "identity": candidate["identity"],
                "namespace_uri": candidate["namespace_uri"],
                "item_id": candidate["item_id"],
                "labels": sorted(candidate["labels"]),
                "node_types": sorted(candidate["node_types"]),
                "graph_kinds": sorted(candidate["graph_kinds"]),
                "layers": sorted(candidate["layers"]),
                "evidence_refs": sorted(candidate["evidence_refs"]),
                "trust_levels": sorted(candidate["trust_levels"]),
                "score": round(float(candidate["score"]), 6),
                "reasons": sorted(candidate["reasons"], key=lambda row: _canonical(row)),
            })
        rows.sort(key=lambda row: (-row["score"], row["identity"]))
        rows = rows[:limit]

        coverage = {
            graph_kind: sum(1 for row in rows if graph_kind in row["graph_kinds"])
            for graph_kind in sorted(available_graphs)
        }
        receipt_basis = {
            "schema_version": 1,
            "task": normalized_task,
            "limit": limit,
            "max_hops": max_hops,
            "required_graphs": sorted(required),
            "include_tainted": include_tainted,
            "layers": [
                {
                    "name": layer.name,
                    "graph_kind": layer.graph_kind,
                    "node_count": len(layer.nodes),
                    "edge_count": len(layer.edges),
                    "source_refs": list(layer.source_refs),
                }
                for layer in sorted(self._layers.values(), key=lambda value: value.name)
            ],
            "candidate_identities": [row["identity"] for row in rows],
            "candidate_scores": [row["score"] for row in rows],
            "blocked_identity_count": len(blocked),
        }
        return {
            "schema_version": 1,
            "task": normalized_task,
            "candidates": rows,
            "candidate_count": len(rows),
            "graph_coverage": coverage,
            "available_graphs": sorted(available_graphs),
            "required_graphs": sorted(required),
            "blocked_identity_count": len(blocked),
            "receipt": {
                **receipt_basis,
                "receipt_hash": _hash(receipt_basis),
            },
        }

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "layers": [
                {
                    "name": layer.name,
                    "graph_kind": layer.graph_kind,
                    "node_count": len(layer.nodes),
                    "edge_count": len(layer.edges),
                }
                for layer in sorted(self._layers.values(), key=lambda value: value.name)
            ],
            "graph_kinds": sorted({layer.graph_kind for layer in self._layers.values()}),
            "persistent_store": False,
            "payload_authority": False,
            "max_query_limit": 100,
            "max_hops": 4,
        }


__all__ = [
    "GRAPH_KINDS",
    "GraphNode",
    "GraphEdge",
    "GraphLayer",
    "MultiGraphRetrieval",
]
