from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
EDGE_TYPES = {
    "defines", "calls", "imports", "inherits", "implements", "overrides",
    "reads", "writes", "instantiates", "data-flow", "taint-flow",
    "tested-by", "builds", "depends-on", "stacktrace", "renamed-from",
    "owned-by", "changed-with",
}


def _tokens(value: str) -> set[str]:
    values: set[str] = set()
    for token in _TOKEN_RE.findall(value):
        normalized = token.casefold()
        if len(normalized) > 1:
            values.add(normalized)
        for part in re.split(r"[._/:-]+", normalized):
            if len(part) > 1:
                values.add(part)
    return values


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    kind: str
    qualified_name: str
    path: str
    start_line: int = 0
    end_line: int = 0
    language: str = "unknown"
    evidence_ref: str = ""
    change_frequency: float = 0.0
    ownership: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    edge_type: str
    confidence: float = 1.0
    evidence_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StructuralQueryResult:
    node: GraphNode
    score: float
    matched_terms: tuple[str, ...]
    inbound_edges: int
    outbound_edges: int
    reasons: tuple[str, ...]


class SemanticGraph:
    """Deterministic multi-graph for repository navigation and impact analysis."""

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.outbound: dict[str, list[GraphEdge]] = {}
        self.inbound: dict[str, list[GraphEdge]] = {}

    def add_node(self, node: GraphNode) -> None:
        if not node.node_id or not node.qualified_name or not node.path:
            raise ValueError("node identity is incomplete")
        self.nodes[node.node_id] = node
        self.outbound.setdefault(node.node_id, [])
        self.inbound.setdefault(node.node_id, [])

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.edge_type not in EDGE_TYPES:
            raise ValueError(f"unsupported edge type: {edge.edge_type}")
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise KeyError("edge endpoints must exist")
        if not 0.0 <= edge.confidence <= 1.0:
            raise ValueError("edge confidence must be between 0 and 1")
        identity = (edge.source, edge.target, edge.edge_type, edge.evidence_ref)
        if not any((item.source, item.target, item.edge_type, item.evidence_ref) == identity for item in self.outbound[edge.source]):
            self.outbound[edge.source].append(edge)
            self.inbound[edge.target].append(edge)

    def ingest_snapshot(self, snapshot: dict[str, Any]) -> dict[str, int]:
        for raw in snapshot.get("nodes", []):
            values = dict(raw)
            values["ownership"] = tuple(values.get("ownership", ()))
            self.add_node(GraphNode(**values))
        for raw in snapshot.get("edges", []):
            self.add_edge(GraphEdge(**raw))
        return {"nodes": len(self.nodes), "edges": sum(map(len, self.outbound.values()))}

    def query(self, query: str, *, limit: int = 20, path_hint: str = "", owner_hint: str = "") -> list[StructuralQueryResult]:
        query_terms = _tokens(query)
        path_terms = _tokens(path_hint)
        owner = owner_hint.casefold()
        rows: list[StructuralQueryResult] = []
        for node in self.nodes.values():
            corpus = _tokens(f"{node.qualified_name} {node.path} {node.kind} {node.language}")
            matched = tuple(sorted(query_terms & corpus))
            if query_terms and not matched and not (path_terms & _tokens(node.path)):
                continue
            lexical = len(matched) / max(1, len(query_terms))
            exact = 1.0 if query.casefold() in node.qualified_name.casefold() else 0.0
            path_score = len(path_terms & _tokens(node.path)) / max(1, len(path_terms)) if path_terms else 0.0
            owner_score = 1.0 if owner and owner in {item.casefold() for item in node.ownership} else 0.0
            inbound = len(self.inbound.get(node.node_id, ()))
            outbound = len(self.outbound.get(node.node_id, ()))
            centrality = math.log1p(inbound + outbound) / 5.0
            change = min(1.0, max(0.0, node.change_frequency))
            score = lexical * 50 + exact * 20 + path_score * 10 + owner_score * 5 + centrality * 8 + change * 7
            reasons = []
            if matched: reasons.append("lexical")
            if exact: reasons.append("exact-qualified-name")
            if path_score: reasons.append("path-hint")
            if owner_score: reasons.append("ownership")
            if centrality: reasons.append("graph-centrality")
            if change: reasons.append("change-frequency")
            rows.append(StructuralQueryResult(node, score, matched, inbound, outbound, tuple(reasons)))
        return sorted(rows, key=lambda item: (-item.score, item.node.path, item.node.start_line, item.node.node_id))[:max(1, limit)]

    def traverse(self, start: Iterable[str], *, edge_types: Iterable[str] | None = None, reverse: bool = False, max_depth: int = 6) -> list[str]:
        allowed = set(edge_types or EDGE_TYPES)
        queue = [(node_id, 0) for node_id in start if node_id in self.nodes]
        seen = {node_id for node_id, _ in queue}
        ordered = [node_id for node_id, _ in queue]
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            edges = self.inbound[current] if reverse else self.outbound[current]
            for edge in sorted(edges, key=lambda item: (item.edge_type, item.target, item.source)):
                if edge.edge_type not in allowed:
                    continue
                candidate = edge.source if reverse else edge.target
                if candidate not in seen:
                    seen.add(candidate)
                    ordered.append(candidate)
                    queue.append((candidate, depth + 1))
        return ordered

    def impact(self, node_id: str, *, max_depth: int = 8) -> dict[str, Any]:
        impacted = self.traverse((node_id,), edge_types=("calls", "imports", "depends-on", "implements", "overrides", "tested-by", "builds"), reverse=True, max_depth=max_depth)
        tests = [value for value in impacted if self.nodes[value].kind in {"test", "test-case", "test-suite"}]
        return {
            "root": node_id,
            "impacted": [asdict(self.nodes[value]) for value in impacted],
            "affected_tests": [asdict(self.nodes[value]) for value in tests],
            "exact_evidence_complete": all(bool(self.nodes[value].evidence_ref) for value in impacted),
        }

    def stacktrace_resolve(self, frames: Iterable[dict[str, Any]]) -> list[StructuralQueryResult]:
        results: list[StructuralQueryResult] = []
        seen: set[str] = set()
        for frame in frames:
            query = str(frame.get("symbol") or frame.get("function") or "")
            path = str(frame.get("path") or frame.get("file") or "")
            for result in self.query(query, path_hint=path, limit=5):
                if result.node.node_id not in seen:
                    seen.add(result.node.node_id)
                    results.append(result)
        return results

    def validate(self) -> dict[str, Any]:
        reasons: list[str] = []
        for node_id, edges in self.outbound.items():
            if node_id not in self.nodes:
                reasons.append(f"missing-node:{node_id}")
            for edge in edges:
                if edge.target not in self.nodes:
                    reasons.append(f"missing-target:{edge.target}")
                if not edge.evidence_ref:
                    reasons.append(f"missing-evidence:{edge.source}->{edge.target}:{edge.edge_type}")
        return {"ok": not reasons, "nodes": len(self.nodes), "edges": sum(map(len, self.outbound.values())), "reasons": reasons}
