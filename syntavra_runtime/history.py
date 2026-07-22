from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .state import StateDB
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class HistoryEvent:
    seq: int
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: float


class ImmutableHistory:
    """Hash-chained immutable history plus recursively expandable summary DAG."""

    def __init__(self, path: Path, *, session_id: str):
        self.state = StateDB(path)
        self.session_id = session_id
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_events(
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id,seq),
                    UNIQUE(session_id,event_hash)
                );
                CREATE TABLE IF NOT EXISTS summary_nodes(
                    summary_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    parent_ids_json TEXT NOT NULL,
                    source_event_seqs_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS summary_session_idx
                    ON summary_nodes(session_id,created_at);
                """
            )

    def append(self, event_type: str, payload: dict[str, Any]) -> HistoryEvent:
        with self.state.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT seq,event_hash FROM history_events WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            seq = int(row["seq"]) + 1 if row else 1
            previous = row["event_hash"] if row else "0" * 64
            created = time.time()
            material = {
                "session_id": self.session_id,
                "seq": seq,
                "event_type": event_type,
                "payload": payload,
                "previous_hash": previous,
                "created_at": created,
            }
            digest = sha256_bytes(canonical_json(material))
            db.execute(
                "INSERT INTO history_events VALUES(?,?,?,?,?,?,?)",
                (
                    self.session_id,
                    seq,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    previous,
                    digest,
                    created,
                ),
            )
        return HistoryEvent(seq, event_type, payload, previous, digest, created)

    def verify_chain(self) -> bool:
        previous = "0" * 64
        with self.state.read() as db:
            rows = db.execute(
                "SELECT * FROM history_events WHERE session_id=? ORDER BY seq",
                (self.session_id,),
            ).fetchall()
        for expected, row in enumerate(rows, 1):
            payload = json.loads(row["payload_json"])
            material = {
                "session_id": self.session_id,
                "seq": expected,
                "event_type": row["event_type"],
                "payload": payload,
                "previous_hash": previous,
                "created_at": row["created_at"],
            }
            digest = sha256_bytes(canonical_json(material))
            if row["seq"] != expected or row["previous_hash"] != previous or row["event_hash"] != digest:
                return False
            previous = digest
        return True

    def create_summary(
        self,
        content: str,
        *,
        parent_ids: Iterable[str],
        source_event_seqs: Iterable[int],
    ) -> str:
        parents = tuple(dict.fromkeys(parent_ids))
        seqs = tuple(sorted(set(int(value) for value in source_event_seqs)))
        payload = {
            "session_id": self.session_id,
            "content": content,
            "parent_ids": parents,
            "source_event_seqs": seqs,
        }
        summary_id = "sum_" + sha256_bytes(canonical_json(payload))[:20]
        with self.state.transaction(immediate=True) as db:
            for parent in parents:
                if not db.execute(
                    "SELECT 1 FROM summary_nodes WHERE summary_id=? AND session_id=?",
                    (parent, self.session_id),
                ).fetchone():
                    raise KeyError(parent)
            if seqs:
                placeholders = ",".join("?" for _ in seqs)
                found = db.execute(
                    f"SELECT COUNT(*) FROM history_events WHERE session_id=? AND seq IN ({placeholders})",
                    [self.session_id, *seqs],
                ).fetchone()[0]
                if found != len(seqs):
                    raise KeyError("summary references missing event")
            db.execute(
                "INSERT OR IGNORE INTO summary_nodes VALUES(?,?,?,?,?,?)",
                (
                    summary_id,
                    self.session_id,
                    content,
                    json.dumps(parents),
                    json.dumps(seqs),
                    time.time(),
                ),
            )
        return summary_id

    def compact(self, *, leaf_size: int = 32, fanout: int = 8) -> str | None:
        if leaf_size < 1 or fanout < 2:
            raise ValueError("invalid compaction shape")
        with self.state.read() as db:
            seqs = [
                int(row[0])
                for row in db.execute(
                    "SELECT seq FROM history_events WHERE session_id=? ORDER BY seq",
                    (self.session_id,),
                )
            ]
        if not seqs:
            return None
        level: list[str] = []
        for start in range(0, len(seqs), leaf_size):
            batch = seqs[start : start + leaf_size]
            level.append(
                self.create_summary(
                    f"events {batch[0]}-{batch[-1]}",
                    parent_ids=(),
                    source_event_seqs=batch,
                )
            )
        depth = 0
        while len(level) > 1:
            next_level: list[str] = []
            for start in range(0, len(level), fanout):
                parents = level[start : start + fanout]
                next_level.append(
                    self.create_summary(
                        f"summary level {depth + 1}: {len(parents)} nodes",
                        parent_ids=parents,
                        source_event_seqs=(),
                    )
                )
            level = next_level
            depth += 1
        return level[0]

    def expand_summary(self, summary_id: str) -> dict[str, Any]:
        with self.state.read() as db:
            rows = {
                row["summary_id"]: row
                for row in db.execute(
                    "SELECT * FROM summary_nodes WHERE session_id=?",
                    (self.session_id,),
                )
            }
            if summary_id not in rows:
                raise KeyError(summary_id)
            events_by_seq = {
                int(row["seq"]): dict(row)
                for row in db.execute(
                    "SELECT * FROM history_events WHERE session_id=? ORDER BY seq",
                    (self.session_id,),
                )
            }
        visited: set[str] = set()
        ordered_seqs: set[int] = set()
        nodes: list[dict[str, Any]] = []

        def walk(node_id: str) -> None:
            if node_id in visited:
                return
            visited.add(node_id)
            row = rows.get(node_id)
            if row is None:
                raise KeyError(node_id)
            parents = tuple(json.loads(row["parent_ids_json"]))
            seqs = tuple(int(value) for value in json.loads(row["source_event_seqs_json"]))
            nodes.append({"summary_id": node_id, "content": row["content"], "parents": parents, "event_seqs": seqs})
            ordered_seqs.update(seqs)
            for parent in parents:
                walk(parent)

        walk(summary_id)
        events: list[dict[str, Any]] = []
        for seq in sorted(ordered_seqs):
            event = dict(events_by_seq[seq])
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        root = rows[summary_id]
        return {
            "summary_id": summary_id,
            "content": root["content"],
            "parents": json.loads(root["parent_ids_json"]),
            "nodes": nodes,
            "events": events,
            "coverage": len(events),
        }
