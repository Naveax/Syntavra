from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


_QUERY_TOKEN_RE = re.compile(r"[^\W_]+|[A-Za-z0-9_./:-]+", re.UNICODE)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.casefold() for token in _QUERY_TOKEN_RE.findall(text) if len(token) > 1))


class RepositoryQueryEngine:
    """Indexed repository search over the canonical semantic graph database.

    FTS5 is preferred, while an indexed SQL fallback remains available on Python
    builds without FTS5. Query execution never materializes the complete node
    table in Python.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.backend = "sqlite-like"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
                CREATE INDEX IF NOT EXISTS idx_nodes_kind_language ON nodes(kind, language);
                CREATE INDEX IF NOT EXISTS idx_edges_source_type ON edges(source, edge_type);
                CREATE INDEX IF NOT EXISTS idx_edges_target_type ON edges(target, edge_type);
                """
            )
            try:
                db.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(
                           node_id UNINDEXED,
                           name,
                           qualified_name,
                           path,
                           kind,
                           language,
                           tokenize='unicode61 remove_diacritics 2'
                       )"""
                )
            except sqlite3.OperationalError:
                self.backend = "sqlite-like"
            else:
                self.backend = "sqlite-fts5"

    def refresh(self, node_ids: Iterable[str] | None = None) -> dict[str, Any]:
        with self._connect() as db:
            if self.backend != "sqlite-fts5":
                count = int(db.execute("SELECT COUNT(*) FROM nodes WHERE kind != 'external'").fetchone()[0])
                return {"backend": self.backend, "indexed_nodes": count, "incremental": False}
            ids = tuple(dict.fromkeys(str(value) for value in (node_ids or ())))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM node_search WHERE node_id IN ({placeholders})", ids)
                rows = db.execute(
                    f"SELECT node_id,name,qualified_name,path,kind,language FROM nodes WHERE kind != 'external' AND node_id IN ({placeholders})",
                    ids,
                ).fetchall()
            else:
                db.execute("DELETE FROM node_search")
                rows = db.execute(
                    "SELECT node_id,name,qualified_name,path,kind,language FROM nodes WHERE kind != 'external'"
                ).fetchall()
            db.executemany(
                "INSERT INTO node_search(node_id,name,qualified_name,path,kind,language) VALUES(?,?,?,?,?,?)",
                [tuple(row) for row in rows],
            )
            return {"backend": self.backend, "indexed_nodes": len(rows), "incremental": bool(ids)}

    @staticmethod
    def _match_expression(tokens: tuple[str, ...]) -> str:
        escaped = [token.replace('"', '""') for token in tokens]
        return " OR ".join(f'"{token}"*' for token in escaped)

    @staticmethod
    def _metadata(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return json.loads(row["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def _degree_map(self, db: sqlite3.Connection, node_ids: tuple[str, ...]) -> dict[str, int]:
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        rows = db.execute(
            f"""SELECT node_id, COUNT(*) degree FROM (
                    SELECT source node_id FROM edges WHERE source IN ({placeholders})
                    UNION ALL
                    SELECT target node_id FROM edges WHERE target IN ({placeholders})
                ) GROUP BY node_id""",
            (*node_ids, *node_ids),
        ).fetchall()
        return {str(row["node_id"]): int(row["degree"]) for row in rows}

    def query(self, text: str, *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        normalized = text.strip().casefold()
        terms = _tokens(text)
        candidate_limit = max(40, limit * 8)
        rows: list[sqlite3.Row] = []
        rank_by_id: dict[str, float] = {}

        with self._connect() as db:
            if normalized:
                exact = db.execute(
                    """SELECT * FROM nodes
                       WHERE kind != 'external' AND (lower(name) = ? OR lower(qualified_name) = ?)
                       ORDER BY path,start_line LIMIT ?""",
                    (normalized, normalized, candidate_limit),
                ).fetchall()
                for row in exact:
                    rows.append(row)
                    rank_by_id[str(row["node_id"])] = 120.0

            if self.backend == "sqlite-fts5" and terms:
                try:
                    hits = db.execute(
                        """SELECT n.*, bm25(node_search, 0.0, 8.0, 5.0, 3.0, 1.0, 1.0) fts_rank
                           FROM node_search JOIN nodes n USING(node_id)
                           WHERE node_search MATCH ?
                           ORDER BY fts_rank LIMIT ?""",
                        (self._match_expression(terms), candidate_limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    hits = []
                for row in hits:
                    node_id = str(row["node_id"])
                    if node_id not in rank_by_id:
                        rows.append(row)
                    rank_by_id[node_id] = max(rank_by_id.get(node_id, 0.0), 70.0 + max(-20.0, -float(row["fts_rank"])))

            if not rows:
                pattern = f"%{normalized}%"
                rows = db.execute(
                    """SELECT * FROM nodes
                       WHERE kind != 'external' AND (
                           lower(name) LIKE ? OR lower(qualified_name) LIKE ? OR lower(path) LIKE ?
                       ) ORDER BY path,start_line LIMIT ?""",
                    (pattern, pattern, pattern, candidate_limit),
                ).fetchall()
                for row in rows:
                    rank_by_id[str(row["node_id"])] = 40.0

            unique_rows: dict[str, sqlite3.Row] = {str(row["node_id"]): row for row in rows}
            ids = tuple(unique_rows)
            degrees = self._degree_map(db, ids)

        scored: list[tuple[float, dict[str, Any]]] = []
        query_terms = set(terms)
        for node_id, row in unique_rows.items():
            metadata = self._metadata(row)
            corpus_terms = set(_tokens(f"{row['name']} {row['qualified_name']} {row['path']} {row['kind']} {row['language']}"))
            matched = sorted(query_terms & corpus_terms)
            exact_name = normalized in {str(row["name"]).casefold(), str(row["qualified_name"]).casefold()}
            semantic_bonus = 10.0 if metadata.get("exact_semantic") else 4.0 if metadata.get("exact_syntax") else 0.0
            degree = degrees.get(node_id, 0)
            score = rank_by_id.get(node_id, 0.0) + semantic_bonus + min(12.0, degree * 0.4) + (20.0 if exact_name else 0.0)
            value = dict(row)
            value.pop("fts_rank", None)
            value["metadata"] = metadata
            value["score"] = round(score, 6)
            value["matched_terms"] = matched
            value["degree"] = degree
            value["semantic_status"] = "exact" if metadata.get("exact_semantic") else "syntax" if metadata.get("exact_syntax") else "candidate"
            value["query_backend"] = self.backend
            scored.append((score, value))

        scored.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"], item[1]["node_id"]))
        return [value for _, value in scored[:limit]]

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            graph_nodes = int(db.execute("SELECT COUNT(*) FROM nodes WHERE kind != 'external'").fetchone()[0])
            indexed_nodes = (
                int(db.execute("SELECT COUNT(*) FROM node_search").fetchone()[0])
                if self.backend == "sqlite-fts5"
                else graph_nodes
            )
        return {"backend": self.backend, "graph_nodes": graph_nodes, "indexed_nodes": indexed_nodes}


__all__ = ["RepositoryQueryEngine"]
