from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .bounded_process import run_bounded_process
from .platform_common import _connect, _now
from .semantic_indexes import SemanticIndexBundle, load_semantic_index


class SemanticIndexStore:
    """Atomic ownership layer for imported LSIF/SCIP graph material.

    Imported nodes and edges coexist with syntax/runtime graph rows, but every
    imported row is owned by a source key. Re-importing a source replaces only
    rows owned by that source. Stale indexes never retain exact-semantic status.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        with _connect(database_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_sources (
                    source_key TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    repository_commit TEXT,
                    current_commit TEXT,
                    stale INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    node_count INTEGER NOT NULL,
                    edge_count INTEGER NOT NULL,
                    diagnostics_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS semantic_source_nodes (
                    source_key TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    PRIMARY KEY(source_key, node_id),
                    FOREIGN KEY(source_key) REFERENCES semantic_sources(source_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_source_nodes_node ON semantic_source_nodes(node_id);
                CREATE TABLE IF NOT EXISTS semantic_source_edges (
                    source_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    PRIMARY KEY(source_key, source, target, edge_type, evidence_ref),
                    FOREIGN KEY(source_key) REFERENCES semantic_sources(source_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_source_edges_source ON semantic_source_edges(source);
                CREATE INDEX IF NOT EXISTS idx_semantic_source_edges_target ON semantic_source_edges(target);
                """
            )

    @staticmethod
    def repository_commit(repository_root: Path) -> str | None:
        root = repository_root.resolve(strict=True)
        environment = {"PATH": os.environ.get("PATH", "")}
        result = run_bounded_process(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            cwd=str(root),
            environment=environment,
            input_bytes=None,
            timeout_seconds=5,
            stdout_limit=256,
            stderr_limit=4096,
            start_new_session=os.name != "nt",
        )
        if not result.ok:
            return None
        value = result.stdout.decode("ascii", errors="ignore").strip().casefold()
        return value if len(value) == 40 and all(character in "0123456789abcdef" for character in value) else None

    @staticmethod
    def source_key(index_path: Path, format: str, source_name: str | None = None) -> str:
        identity = source_name or str(index_path.expanduser().resolve(strict=False))
        return "semantic-source:" + hashlib.sha256(f"{format.casefold()}\0{identity}".encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata(value: Mapping[str, Any], *, stale: bool, source_key: str, source_sha256: str) -> str:
        metadata = dict(value)
        metadata.update(
            {
                "semantic_source_key": source_key,
                "semantic_source_sha256": source_sha256,
                "stale_semantic_index": stale,
            }
        )
        if stale:
            metadata["exact_semantic"] = False
            metadata["confidence_capped_by_staleness"] = True
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _freshness(bundle: SemanticIndexBundle, current_commit: str | None) -> tuple[bool, str | None, str | None]:
        index_commit = bundle.repository_commit.casefold() if bundle.repository_commit else None
        current = current_commit.casefold() if current_commit else None
        stale = bool(index_commit and current and index_commit != current)
        return stale, index_commit, current

    def _remove_owned_rows(self, db: Any, source_key: str) -> None:
        owned_edges = [dict(row) for row in db.execute(
            "SELECT source,target,edge_type,evidence_ref FROM semantic_source_edges WHERE source_key = ?",
            (source_key,),
        )]
        for edge in owned_edges:
            still_owned = db.execute(
                """SELECT 1 FROM semantic_source_edges
                   WHERE source_key != ? AND source = ? AND target = ? AND edge_type = ? AND evidence_ref = ? LIMIT 1""",
                (source_key, edge["source"], edge["target"], edge["edge_type"], edge["evidence_ref"]),
            ).fetchone()
            if not still_owned:
                db.execute(
                    "DELETE FROM edges WHERE source = ? AND target = ? AND edge_type = ? AND evidence_ref = ?",
                    (edge["source"], edge["target"], edge["edge_type"], edge["evidence_ref"]),
                )

        owned_nodes = [row["node_id"] for row in db.execute(
            "SELECT node_id FROM semantic_source_nodes WHERE source_key = ?",
            (source_key,),
        )]
        for node_id in owned_nodes:
            still_owned = db.execute(
                "SELECT 1 FROM semantic_source_nodes WHERE source_key != ? AND node_id = ? LIMIT 1",
                (source_key, node_id),
            ).fetchone()
            if not still_owned:
                # Imported edges referencing this node were removed above. Do not
                # remove a node if a non-imported graph edge still references it.
                external_reference = db.execute(
                    "SELECT 1 FROM edges WHERE source = ? OR target = ? LIMIT 1",
                    (node_id, node_id),
                ).fetchone()
                if not external_reference:
                    db.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        db.execute("DELETE FROM semantic_source_edges WHERE source_key = ?", (source_key,))
        db.execute("DELETE FROM semantic_source_nodes WHERE source_key = ?", (source_key,))
        db.execute("DELETE FROM semantic_sources WHERE source_key = ?", (source_key,))

    def import_bundle(
        self,
        bundle: SemanticIndexBundle,
        *,
        index_path: Path,
        source_name: str | None = None,
        current_commit: str | None = None,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        key = self.source_key(index_path, bundle.format, source_name)
        stale, index_commit, current = self._freshness(bundle, current_commit)
        if stale and not allow_stale:
            raise ValueError(
                f"semantic index commit mismatch: index={index_commit} repository={current}; pass allow_stale=True only for candidate evidence"
            )

        node_ids = {node.node_id for node in bundle.nodes}
        if len(node_ids) != len(bundle.nodes):
            raise ValueError("semantic index contains duplicate node ids")
        for edge in bundle.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError("semantic index edge references a node outside its bundle")

        with _connect(self.database_path) as db:
            for node_id in node_ids:
                exists = db.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
                if not exists:
                    continue
                owned_by_current = db.execute(
                    "SELECT 1 FROM semantic_source_nodes WHERE source_key = ? AND node_id = ?",
                    (key, node_id),
                ).fetchone()
                owned_by_other = db.execute(
                    "SELECT 1 FROM semantic_source_nodes WHERE source_key != ? AND node_id = ? LIMIT 1",
                    (key, node_id),
                ).fetchone()
                if not owned_by_current or owned_by_other:
                    raise ValueError(f"semantic index node id collision: {node_id}")
            self._remove_owned_rows(db, key)
            db.execute(
                """INSERT INTO semantic_sources
                   (source_key,source_name,source_path,format,source_sha256,repository_commit,current_commit,stale,
                    imported_at,node_count,edge_count,diagnostics_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    source_name or index_path.name,
                    str(index_path.expanduser().resolve(strict=False)),
                    bundle.format,
                    bundle.source_sha256,
                    index_commit,
                    current,
                    1 if stale else 0,
                    _now(),
                    len(bundle.nodes),
                    len(bundle.edges),
                    json.dumps(bundle.diagnostics, ensure_ascii=False, sort_keys=True),
                ),
            )
            for node in bundle.nodes:
                db.execute(
                    """INSERT OR REPLACE INTO nodes
                       (node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        node.node_id,
                        node.path,
                        node.kind,
                        node.name,
                        node.qualified_name,
                        node.start_line,
                        node.end_line,
                        node.language,
                        node.evidence_ref,
                        self._metadata(node.metadata, stale=stale, source_key=key, source_sha256=bundle.source_sha256),
                    ),
                )
                db.execute(
                    "INSERT INTO semantic_source_nodes(source_key,node_id) VALUES(?,?)",
                    (key, node.node_id),
                )
            for edge in bundle.edges:
                confidence = min(float(edge.confidence), 0.6) if stale else float(edge.confidence)
                metadata_json = self._metadata(
                    edge.metadata,
                    stale=stale,
                    source_key=key,
                    source_sha256=bundle.source_sha256,
                )
                db.execute(
                    """INSERT OR REPLACE INTO edges
                       (source,target,edge_type,confidence,evidence_ref,metadata_json)
                       VALUES(?,?,?,?,?,?)""",
                    (edge.source, edge.target, edge.edge_type, confidence, edge.evidence_ref, metadata_json),
                )
                db.execute(
                    """INSERT INTO semantic_source_edges
                       (source_key,source,target,edge_type,evidence_ref) VALUES(?,?,?,?,?)""",
                    (key, edge.source, edge.target, edge.edge_type, edge.evidence_ref),
                )

        return {
            "ok": True,
            "source_key": key,
            "format": bundle.format,
            "source_sha256": bundle.source_sha256,
            "repository_commit": index_commit,
            "current_commit": current,
            "stale": stale,
            "evidence_status": "candidate-stale" if stale else "exact",
            "nodes": len(bundle.nodes),
            "edges": len(bundle.edges),
            "diagnostics": list(bundle.diagnostics),
        }

    def import_path(
        self,
        index_path: Path,
        *,
        repository_root: Path,
        format: str = "auto",
        repository_commit: str | None = None,
        current_commit: str | None = None,
        allow_stale: bool = False,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        root = repository_root.resolve(strict=True)
        current = current_commit if current_commit is not None else self.repository_commit(root)
        bundle = load_semantic_index(
            index_path,
            repository_root=root,
            format=format,
            repository_commit=repository_commit,
        )
        return self.import_bundle(
            bundle,
            index_path=index_path,
            source_name=source_name,
            current_commit=current,
            allow_stale=allow_stale,
        )

    def remove(self, source_key: str) -> dict[str, Any]:
        with _connect(self.database_path) as db:
            exists = db.execute("SELECT 1 FROM semantic_sources WHERE source_key = ?", (source_key,)).fetchone()
            if not exists:
                return {"ok": True, "removed": False, "source_key": source_key}
            self._remove_owned_rows(db, source_key)
        return {"ok": True, "removed": True, "source_key": source_key}

    def stats(self) -> dict[str, Any]:
        with _connect(self.database_path) as db:
            sources = int(db.execute("SELECT COUNT(*) value FROM semantic_sources").fetchone()["value"])
            stale = int(db.execute("SELECT COUNT(*) value FROM semantic_sources WHERE stale = 1").fetchone()["value"])
            nodes = int(db.execute("SELECT COUNT(*) value FROM semantic_source_nodes").fetchone()["value"])
            edges = int(db.execute("SELECT COUNT(*) value FROM semantic_source_edges").fetchone()["value"])
            formats = [dict(row) for row in db.execute(
                "SELECT format,COUNT(*) sources FROM semantic_sources GROUP BY format ORDER BY format"
            )]
        return {
            "semantic_index_sources": sources,
            "stale_semantic_index_sources": stale,
            "semantic_index_nodes": nodes,
            "semantic_index_edges": edges,
            "semantic_index_formats": formats,
        }


__all__ = ["SemanticIndexStore"]
