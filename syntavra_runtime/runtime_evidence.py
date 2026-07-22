from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .platform_common import _connect


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    kind: str
    label: str
    source: str
    confidence: float
    repository_commit: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceEdge:
    source: str
    target: str
    relation: str
    evidence: str
    confidence: float
    repository_commit: str
    observed_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeEvidenceGraph:
    """Evidence-backed runtime relationships that complement the semantic graph.

    Runtime facts are never presented as static exact facts unless their source and
    repository revision are recorded. Repeated observations are deduplicated by a
    canonical evidence hash.
    """

    def __init__(self, path: Path):
        self.path = path
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    repository_commit TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges (
                    evidence TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    repository_commit TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_source ON edges(source, relation);
                CREATE INDEX IF NOT EXISTS idx_evidence_target ON edges(target, relation);
                """
            )

    @staticmethod
    def identity(kind: str, label: str, source: str) -> str:
        return hashlib.sha256(f"{kind}\0{label}\0{source}".encode()).hexdigest()

    def put_node(
        self,
        *,
        kind: str,
        label: str,
        source: str,
        repository_commit: str = "unknown",
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
        node_id: str | None = None,
    ) -> EvidenceNode:
        identifier = node_id or self.identity(kind, label, source)
        record = EvidenceNode(
            node_id=identifier,
            kind=kind,
            label=label,
            source=source,
            confidence=max(0.0, min(1.0, float(confidence))),
            repository_commit=repository_commit,
            metadata=dict(metadata or {}),
        )
        with _connect(self.path) as db:
            db.execute(
                """INSERT OR REPLACE INTO nodes
                   (node_id,kind,label,source,confidence,repository_commit,metadata_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    record.node_id,
                    record.kind,
                    record.label,
                    record.source,
                    record.confidence,
                    record.repository_commit,
                    _canonical(record.metadata),
                ),
            )
        return record

    def put_edge(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        repository_commit: str = "unknown",
        confidence: float = 1.0,
        metadata: Mapping[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> EvidenceEdge:
        timestamp = observed_at or _now()
        body = {
            "source": source,
            "target": target,
            "relation": relation,
            "repository_commit": repository_commit,
            "metadata": dict(metadata or {}),
        }
        evidence = "sha256:" + hashlib.sha256(_canonical(body).encode()).hexdigest()
        record = EvidenceEdge(
            source=source,
            target=target,
            relation=relation,
            evidence=evidence,
            confidence=max(0.0, min(1.0, float(confidence))),
            repository_commit=repository_commit,
            observed_at=timestamp,
            metadata=dict(metadata or {}),
        )
        with _connect(self.path) as db:
            db.execute(
                """INSERT OR REPLACE INTO edges
                   (evidence,source,target,relation,confidence,repository_commit,observed_at,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    record.evidence,
                    record.source,
                    record.target,
                    record.relation,
                    record.confidence,
                    record.repository_commit,
                    record.observed_at,
                    _canonical(record.metadata),
                ),
            )
        return record

    def import_coverage(
        self,
        document: Mapping[str, Any],
        *,
        test_id: str,
        repository_commit: str = "unknown",
    ) -> dict[str, Any]:
        files = document.get("files", {})
        if not isinstance(files, Mapping):
            raise ValueError("coverage document must contain a files object")
        test_node = self.put_node(
            kind="test",
            label=test_id,
            source="coverage",
            repository_commit=repository_commit,
        )
        imported = 0
        for filename, details in files.items():
            if not isinstance(details, Mapping):
                continue
            file_node = self.put_node(
                kind="file",
                label=str(filename),
                source="coverage",
                repository_commit=repository_commit,
            )
            executed = details.get("executed_lines", [])
            missing = details.get("missing_lines", [])
            self.put_edge(
                test_node.node_id,
                file_node.node_id,
                "COVERS",
                repository_commit=repository_commit,
                confidence=1.0,
                metadata={
                    "executed_lines": list(executed) if isinstance(executed, list) else [],
                    "missing_lines": list(missing) if isinstance(missing, list) else [],
                },
            )
            imported += 1
        return {"ok": True, "files": imported, "test": asdict(test_node)}

    def import_trace(
        self,
        spans: Iterable[Mapping[str, Any]],
        *,
        repository_commit: str = "unknown",
    ) -> dict[str, Any]:
        imported = 0
        for span in spans:
            source = str(span.get("source", "")).strip()
            target = str(span.get("target", "")).strip()
            if not source or not target:
                continue
            source_node = self.put_node(
                kind=str(span.get("source_kind", "runtime-symbol")),
                label=source,
                source="trace",
                repository_commit=repository_commit,
            )
            target_node = self.put_node(
                kind=str(span.get("target_kind", "runtime-symbol")),
                label=target,
                source="trace",
                repository_commit=repository_commit,
            )
            self.put_edge(
                source_node.node_id,
                target_node.node_id,
                str(span.get("relation", "RUNTIME_CALL")),
                repository_commit=repository_commit,
                confidence=float(span.get("confidence", 1.0)),
                metadata={key: value for key, value in span.items() if key not in {"source", "target"}},
            )
            imported += 1
        return {"ok": True, "spans": imported}

    def neighbors(self, node_id: str, *, relation: str | None = None, reverse: bool = False) -> list[dict[str, Any]]:
        direction, other = ("target", "source") if reverse else ("source", "target")
        where = f"{direction} = ?" + (" AND relation = ?" if relation else "")
        params: tuple[Any, ...] = (node_id, relation) if relation else (node_id,)
        with _connect(self.path) as db:
            rows = db.execute(f"SELECT * FROM edges WHERE {where} ORDER BY observed_at DESC", params).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json"))
                linked = db.execute("SELECT * FROM nodes WHERE node_id = ?", (row[other],)).fetchone()
                item["node"] = dict(linked) if linked else None
                result.append(item)
        return result

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            nodes = int(db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
            edges = int(db.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
            relations = [dict(row) for row in db.execute("SELECT relation,COUNT(*) count FROM edges GROUP BY relation ORDER BY relation")]
        return {"ok": True, "nodes": nodes, "edges": edges, "relations": relations}


__all__ = ["EvidenceEdge", "EvidenceNode", "RuntimeEvidenceGraph"]
