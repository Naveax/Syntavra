from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .util import canonical_json

GRAPH_SCHEMA_VERSION = 1
CONTRACT_REF_RE = re.compile(r"^(contracts/[A-Za-z0-9_.\-/]+\.json)(?::.*)?$")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _contract_refs(value: Any) -> set[str]:
    refs: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            match = CONTRACT_REF_RE.fullmatch(item.strip())
            if match:
                refs.add(match.group(1))

    visit(value)
    return refs


@dataclass(frozen=True)
class ContractVersionNode:
    path: str
    schema_version: int | str
    family: str
    claim: str
    phase: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "schema_version": self.schema_version,
            "family": self.family,
            "claim": self.claim,
            "phase": self.phase,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ContractDependencyEdge:
    dependent: str
    dependency: str

    def to_dict(self) -> dict[str, str]:
        return {"dependent": self.dependent, "dependency": self.dependency}


class RuntimeContractVersionGraph:
    """Deterministic dependency/version graph for repository contract JSON.

    The graph is metadata-only. It never owns product behavior or persistence.
    Edges point from a dependent contract to a referenced dependency contract.
    """

    def __init__(
        self,
        repo: Path,
        *,
        roots: tuple[str, ...] = ("contracts/python",),
        allow_external_contract_refs: bool = True,
    ):
        self.repo = repo.resolve()
        self.roots = tuple(dict.fromkeys(roots))
        if not self.roots:
            raise ValueError("at least one contract root is required")
        self.allow_external_contract_refs = bool(allow_external_contract_refs)

    def _contract_paths(self) -> list[Path]:
        paths: set[Path] = set()
        for relative in self.roots:
            root = (self.repo / relative).resolve()
            try:
                root.relative_to(self.repo)
            except ValueError as exc:
                raise ValueError(f"contract root escapes repository: {relative}") from exc
            if not root.is_dir():
                raise FileNotFoundError(f"contract root missing: {relative}")
            paths.update(path for path in root.rglob("*.json") if path.is_file())
        return sorted(paths, key=lambda path: path.relative_to(self.repo).as_posix())

    def _read_contract(self, path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"contract must be a JSON object: {path.relative_to(self.repo)}")
        version = value.get("schema_version")
        if not isinstance(version, (int, str)) or isinstance(version, bool):
            raise ValueError(f"contract schema_version missing/invalid: {path.relative_to(self.repo)}")
        return value

    def build(self) -> dict[str, Any]:
        primary_paths = self._contract_paths()
        documents: dict[str, dict[str, Any]] = {}
        queue = deque(primary_paths)

        while queue:
            path = queue.popleft()
            relative = path.relative_to(self.repo).as_posix()
            if relative in documents:
                continue
            document = self._read_contract(path)
            documents[relative] = document

            for target in sorted(_contract_refs(document)):
                target_path = (self.repo / target).resolve()
                try:
                    target_path.relative_to(self.repo)
                except ValueError as exc:
                    raise ValueError(f"contract reference escapes repository: {relative} -> {target}") from exc
                if not target_path.is_file():
                    raise FileNotFoundError(f"missing contract dependency: {relative} -> {target}")
                if target not in documents:
                    if self.allow_external_contract_refs:
                        queue.append(target_path)
                    elif not any(
                        target == root or target.startswith(root.rstrip("/") + "/")
                        for root in self.roots
                    ):
                        raise ValueError(f"external contract dependency forbidden: {relative} -> {target}")

        nodes: list[ContractVersionNode] = []
        edges: set[ContractDependencyEdge] = set()
        for relative in sorted(documents):
            document = documents[relative]
            payload = canonical_json(document)
            nodes.append(
                ContractVersionNode(
                    path=relative,
                    schema_version=document["schema_version"],
                    family=str(document.get("family") or ""),
                    claim=str(document.get("claim") or ""),
                    phase=str(document.get("phase") or ""),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
            for dependency in sorted(_contract_refs(document)):
                if dependency == relative:
                    continue
                if dependency not in documents:
                    raise ValueError(f"unresolved contract dependency: {relative} -> {dependency}")
                edges.add(ContractDependencyEdge(relative, dependency))

        node_rows = [node.to_dict() for node in nodes]
        edge_rows = [edge.to_dict() for edge in sorted(edges, key=lambda edge: (edge.dependent, edge.dependency))]
        identity = {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "roots": list(self.roots),
            "nodes": node_rows,
            "edges": edge_rows,
        }
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "claim": "RUNTIME_CONTRACT_VERSION_GRAPH_V1",
            "roots": list(self.roots),
            "node_count": len(node_rows),
            "edge_count": len(edge_rows),
            "nodes": node_rows,
            "edges": edge_rows,
            "graph_sha256": _digest(identity),
        }

    @staticmethod
    def _snapshot_maps(snapshot: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
        nodes = snapshot.get("nodes")
        edges = snapshot.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValueError("invalid contract graph snapshot")
        node_map: dict[str, dict[str, Any]] = {}
        for raw in nodes:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("path"), str):
                raise ValueError("invalid contract graph node")
            path = str(raw["path"])
            if path in node_map:
                raise ValueError(f"duplicate contract graph node: {path}")
            node_map[path] = _clone(dict(raw))
        edge_set: set[tuple[str, str]] = set()
        for raw in edges:
            if not isinstance(raw, Mapping):
                raise ValueError("invalid contract graph edge")
            dependent = raw.get("dependent")
            dependency = raw.get("dependency")
            if not isinstance(dependent, str) or not isinstance(dependency, str):
                raise ValueError("invalid contract graph edge endpoints")
            edge_set.add((dependent, dependency))
        return node_map, edge_set

    @staticmethod
    def invalidation_plan(
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> dict[str, Any]:
        before_nodes, before_edges = RuntimeContractVersionGraph._snapshot_maps(previous)
        after_nodes, after_edges = RuntimeContractVersionGraph._snapshot_maps(current)

        before_paths = set(before_nodes)
        after_paths = set(after_nodes)
        added = sorted(after_paths - before_paths)
        removed = sorted(before_paths - after_paths)
        changed = sorted(
            path
            for path in before_paths & after_paths
            if before_nodes[path] != after_nodes[path]
        )
        seeds = set(added) | set(removed) | set(changed)

        reverse: dict[str, set[str]] = {}
        for dependent, dependency in before_edges | after_edges:
            reverse.setdefault(dependency, set()).add(dependent)

        affected = set(seeds)
        queue = deque(sorted(seeds))
        while queue:
            dependency = queue.popleft()
            for dependent in sorted(reverse.get(dependency, ())):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)

        invalidated = sorted(path for path in affected if path in after_paths)
        identity = {
            "added_contracts": added,
            "removed_contracts": removed,
            "changed_contracts": changed,
            "invalidated_contracts": invalidated,
        }
        return {
            **identity,
            "changed_count": len(seeds),
            "invalidated_count": len(invalidated),
            "invalidation_sha256": _digest(identity),
        }

    def current_invalidation_plan(self, previous: Mapping[str, Any]) -> dict[str, Any]:
        return self.invalidation_plan(previous, self.build())


__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "ContractVersionNode",
    "ContractDependencyEdge",
    "RuntimeContractVersionGraph",
]
