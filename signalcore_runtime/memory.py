from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .state import StateDB
from .util import sha256_bytes


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    memory_class: str
    text: str
    confidence: float
    provenance: dict[str, Any]
    created_at: float
    superseded_by: str | None = None
    expires_at: float | None = None
    tags: tuple[str, ...] = ()


class PersistentMemory:
    """Scoped memory graph with FTS5 retrieval, provenance and supersession."""

    def __init__(self, path: Path, *, project_id: str, user_id: str = "default"):
        self.state = StateDB(path)
        self.project_id = project_id
        self.user_id = user_id
        self.fts_available = False
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories(
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    memory_class TEXT NOT NULL,
                    text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    provenance_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    superseded_by TEXT,
                    expires_at REAL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY(superseded_by) REFERENCES memories(memory_id)
                );
                CREATE INDEX IF NOT EXISTS memories_scope_idx
                    ON memories(project_id,user_id,memory_class,created_at DESC);
                CREATE TABLE IF NOT EXISTS memory_relations(
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    weight REAL NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(source_id,relation,target_id),
                    FOREIGN KEY(source_id) REFERENCES memories(memory_id),
                    FOREIGN KEY(target_id) REFERENCES memories(memory_id)
                );
                CREATE INDEX IF NOT EXISTS memory_relation_target_idx
                    ON memory_relations(target_id,relation);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
            if "expires_at" not in columns:
                db.execute("ALTER TABLE memories ADD COLUMN expires_at REAL")
            if "tags_json" not in columns:
                db.execute("ALTER TABLE memories ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            try:
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                    "USING fts5(memory_id UNINDEXED, text, tokenize='unicode61')"
                )
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w.-]+", query, flags=re.UNICODE)
        return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:32])

    def add(
        self,
        memory_class: str,
        text: str,
        *,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
        expires_at: float | None = None,
        tags: Iterable[str] = (),
    ) -> MemoryRecord:
        clean = text.strip()
        if not clean:
            raise ValueError("memory text cannot be empty")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence out of range")
        normalized_tags = tuple(sorted({str(tag).strip() for tag in tags if str(tag).strip()}))
        digest = sha256_bytes(
            json.dumps(
                {"class": memory_class, "text": clean, "tags": normalized_tags},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        with self.state.transaction(immediate=True) as db:
            existing = db.execute(
                """
                SELECT * FROM memories
                WHERE project_id=? AND user_id=? AND memory_class=? AND content_hash=?
                  AND superseded_by IS NULL
                """,
                (self.project_id, self.user_id, memory_class, digest),
            ).fetchone()
            if existing:
                return self._record(existing)
            memory_id = uuid.uuid4().hex
            created = time.time()
            db.execute(
                "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (
                    memory_id,
                    self.project_id,
                    self.user_id,
                    memory_class,
                    clean,
                    confidence,
                    json.dumps(provenance or {}, ensure_ascii=False, sort_keys=True),
                    digest,
                    created,
                    expires_at,
                    json.dumps(normalized_tags, ensure_ascii=False),
                ),
            )
            if self.fts_available:
                db.execute("INSERT INTO memories_fts(memory_id,text) VALUES(?,?)", (memory_id, clean))
            row = db.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        return self._record(row)

    def supersede(self, old_id: str, new_id: str) -> None:
        with self.state.transaction(immediate=True) as db:
            if not db.execute(
                "SELECT 1 FROM memories WHERE memory_id=? AND project_id=? AND user_id=?",
                (new_id, self.project_id, self.user_id),
            ).fetchone():
                raise KeyError(new_id)
            cursor = db.execute(
                "UPDATE memories SET superseded_by=? WHERE memory_id=? AND project_id=? AND user_id=?",
                (new_id, old_id, self.project_id, self.user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(old_id)

    def link(self, source_id: str, relation: str, target_id: str, *, weight: float = 1.0) -> None:
        if not relation.strip():
            raise ValueError("relation cannot be empty")
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("weight must be positive and finite")
        with self.state.transaction(immediate=True) as db:
            rows = db.execute(
                "SELECT memory_id FROM memories WHERE memory_id IN (?,?) AND project_id=? AND user_id=?",
                (source_id, target_id, self.project_id, self.user_id),
            ).fetchall()
            if {row[0] for row in rows} != {source_id, target_id}:
                raise KeyError("memory relation scope mismatch")
            db.execute(
                "INSERT OR REPLACE INTO memory_relations VALUES(?,?,?,?,?)",
                (source_id, relation.strip(), target_id, float(weight), time.time()),
            )

    def neighbors(self, memory_id: str, *, relation: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = [memory_id, self.project_id, self.user_id]
        relation_clause = ""
        if relation:
            relation_clause = " AND r.relation=?"
            params.append(relation)
        params.append(max(1, limit))
        with self.state.read() as db:
            rows = db.execute(
                """
                SELECT r.relation,r.weight,m.*
                FROM memory_relations r
                JOIN memories m ON m.memory_id=r.target_id
                WHERE r.source_id=? AND m.project_id=? AND m.user_id=?
                """
                + relation_clause
                + " ORDER BY r.weight DESC,m.created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {"relation": row["relation"], "weight": row["weight"], "memory": asdict(self._record(row))}
            for row in rows
        ]

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        memory_classes: Iterable[str] = (),
        include_superseded: bool = False,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        classes = tuple(memory_classes)
        params: list[Any] = [self.project_id, self.user_id]
        clauses = ["m.project_id=?", "m.user_id=?"]
        if not include_superseded:
            clauses.append("m.superseded_by IS NULL")
        if not include_expired:
            clauses.append("(m.expires_at IS NULL OR m.expires_at>?)")
            params.append(time.time())
        if classes:
            clauses.append(f"m.memory_class IN ({','.join('?' for _ in classes)})")
            params.extend(classes)
        rows: list[sqlite3.Row] = []
        mode = "LEXICAL_ONLY"
        now = time.time()
        with self.state.read() as db:
            fts_query = self._fts_query(query)
            if self.fts_available and fts_query:
                try:
                    rows = db.execute(
                        """
                        SELECT m.*,bm25(memories_fts) AS lexical_rank,
                               COALESCE((SELECT SUM(r.weight) FROM memory_relations r WHERE r.target_id=m.memory_id),0) AS relation_weight
                        FROM memories_fts
                        JOIN memories m ON m.memory_id=memories_fts.memory_id
                        WHERE memories_fts MATCH ? AND """
                        + " AND ".join(clauses)
                        + " ORDER BY lexical_rank LIMIT ?",
                        [fts_query, *params, max(limit * 4, limit)],
                    ).fetchall()
                    mode = "FTS5_GRAPH"
                except sqlite3.OperationalError:
                    mode = "LEXICAL_DEGRADED"
            if not rows:
                rows = db.execute(
                    """
                    SELECT m.*,0 AS lexical_rank,COALESCE(SUM(r.weight),0) AS relation_weight
                    FROM memories m
                    LEFT JOIN memory_relations r ON r.target_id=m.memory_id
                    WHERE m.text LIKE ? AND """
                    + " AND ".join(clauses)
                    + " GROUP BY m.memory_id ORDER BY m.created_at DESC LIMIT ?",
                    [f"%{query}%", *params, max(limit * 4, limit)],
                ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            lexical = 1.0 / (1.0 + max(0.0, abs(float(row["lexical_rank"]))))
            age_days = max(0.0, (now - float(row["created_at"])) / 86400.0)
            recency = math.exp(-age_days / 90.0)
            relation_boost = min(1.0, float(row["relation_weight"]) / 5.0)
            score = 0.55 * lexical + 0.25 * float(row["confidence"]) + 0.12 * recency + 0.08 * relation_boost
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], -float(item[1]["created_at"]), item[1]["memory_id"]))
        return {
            "mode": mode,
            "results": [
                {**asdict(self._record(row)), "score": score}
                for score, row in scored[: max(1, limit)]
            ],
        }

    @staticmethod
    def _record(row) -> MemoryRecord:
        keys = set(row.keys())
        return MemoryRecord(
            row["memory_id"],
            row["memory_class"],
            row["text"],
            float(row["confidence"]),
            json.loads(row["provenance_json"]),
            float(row["created_at"]),
            row["superseded_by"],
            row["expires_at"] if "expires_at" in keys else None,
            tuple(json.loads(row["tags_json"])) if "tags_json" in keys else (),
        )
