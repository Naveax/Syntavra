from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .state import StateDB
from .util import atomic_write_json, canonical_json, sha256_bytes


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    project_id: str
    parent_ids: tuple[str, ...]
    state: str
    created_at: float
    updated_at: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: float


@dataclass(frozen=True)
class SessionCheckpoint:
    checkpoint_id: str
    session_id: str
    through_sequence: int
    root_summary_id: str | None
    event_hash: str
    created_at: float
    metadata: dict[str, Any]


class SessionRuntime:
    """Crash-safe long-session engine with exact event history and async compaction."""

    def __init__(self, path: Path, *, project_id: str):
        self.state = StateDB(path)
        self.project_id = project_id
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._initialize()

    def _initialize(self) -> None:
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions(
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_ids_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_events(
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id,sequence),
                    UNIQUE(session_id,event_hash),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS session_event_hash_idx ON session_events(event_hash);
                CREATE TABLE IF NOT EXISTS session_summaries(
                    summary_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_start INTEGER NOT NULL,
                    source_end INTEGER NOT NULL,
                    child_ids_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    order_level INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    invalidated_at REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS session_summary_range_idx ON session_summaries(session_id,source_start,source_end);
                CREATE TABLE IF NOT EXISTS session_checkpoints(
                    checkpoint_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    through_sequence INTEGER NOT NULL,
                    root_summary_id TEXT,
                    event_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS session_quarantine(
                    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    def create_session(
        self,
        *,
        session_id: str | None = None,
        parent_ids: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        session_id = session_id or "sess-" + uuid.uuid4().hex
        parents = tuple(dict.fromkeys(str(value) for value in parent_ids))
        now = time.time()
        with self.state.transaction(immediate=True) as db:
            for parent in parents:
                row = db.execute("SELECT project_id FROM sessions WHERE session_id=?", (parent,)).fetchone()
                if not row or row[0] != self.project_id:
                    raise KeyError(f"invalid parent session: {parent}")
            db.execute(
                "INSERT INTO sessions(session_id,project_id,parent_ids_json,state,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
                (session_id, self.project_id, json.dumps(parents), "ACTIVE", now, now, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self.state.read() as db:
            row = db.execute("SELECT * FROM sessions WHERE session_id=? AND project_id=?", (session_id, self.project_id)).fetchone()
        if not row:
            raise KeyError(session_id)
        return SessionRecord(
            row["session_id"], row["project_id"], tuple(json.loads(row["parent_ids_json"])), row["state"],
            float(row["created_at"]), float(row["updated_at"]), json.loads(row["metadata_json"]),
        )

    def list_sessions(self, *, state: str | None = None) -> list[SessionRecord]:
        sql = "SELECT * FROM sessions WHERE project_id=?"
        params: list[Any] = [self.project_id]
        if state:
            sql += " AND state=?"
            params.append(state)
        sql += " ORDER BY updated_at DESC"
        with self.state.read() as db:
            rows = db.execute(sql, params).fetchall()
        return [
            SessionRecord(row["session_id"], row["project_id"], tuple(json.loads(row["parent_ids_json"])), row["state"], float(row["created_at"]), float(row["updated_at"]), json.loads(row["metadata_json"]))
            for row in rows
        ]

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> SessionEvent:
        self.get_session(session_id)
        with self.state.transaction(immediate=True) as db:
            previous = db.execute(
                "SELECT sequence,event_hash FROM session_events WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            sequence = int(previous["sequence"]) + 1 if previous else 1
            previous_hash = previous["event_hash"] if previous else "0" * 64
            created = time.time()
            value = {
                "session_id": session_id,
                "sequence": sequence,
                "event_type": event_type,
                "payload": payload,
                "previous_hash": previous_hash,
                "created_at": created,
            }
            event_hash = sha256_bytes(canonical_json(value))
            db.execute(
                "INSERT INTO session_events(session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                (session_id, sequence, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), previous_hash, event_hash, created),
            )
            db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (created, session_id))
            # Any summary that overlaps a newly appended sequence is impossible by construction,
            # but summaries beyond a rollback/fork boundary are invalidated explicitly elsewhere.
        return SessionEvent(session_id, sequence, event_type, payload, previous_hash, event_hash, created)

    def events(self, session_id: str, *, after: int = 0, limit: int = 1000) -> list[SessionEvent]:
        self.get_session(session_id)
        with self.state.read() as db:
            rows = db.execute(
                "SELECT * FROM session_events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (session_id, max(0, after), max(1, limit)),
            ).fetchall()
        return [
            SessionEvent(row["session_id"], int(row["sequence"]), row["event_type"], json.loads(row["payload_json"]), row["previous_hash"], row["event_hash"], float(row["created_at"]))
            for row in rows
        ]

    def verify(self, session_id: str) -> dict[str, Any]:
        reasons: list[str] = []
        previous = "0" * 64
        expected_sequence = 1
        for event in self.events(session_id, limit=10_000_000):
            if event.sequence != expected_sequence:
                reasons.append(f"sequence-gap:{expected_sequence}->{event.sequence}")
            if event.previous_hash != previous:
                reasons.append(f"previous-hash-mismatch:{event.sequence}")
            value = {
                "session_id": event.session_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "payload": event.payload,
                "previous_hash": event.previous_hash,
                "created_at": event.created_at,
            }
            if sha256_bytes(canonical_json(value)) != event.event_hash:
                reasons.append(f"event-hash-mismatch:{event.sequence}")
            previous = event.event_hash
            expected_sequence = event.sequence + 1
        return {"ok": not reasons, "events": expected_sequence - 1, "last_hash": previous, "reasons": reasons}

    @staticmethod
    def _deterministic_summary(events: list[SessionEvent]) -> str:
        counts: dict[str, int] = {}
        facts: list[str] = []
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
            payload = event.payload
            for key in ("task", "decision", "error", "result", "path", "command", "claim"):
                value = payload.get(key)
                if value is not None and len(facts) < 20:
                    facts.append(f"#{event.sequence} {key}={str(value)[:400]}")
        return "Events " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) + ("\n" + "\n".join(facts) if facts else "")

    def compact(
        self,
        session_id: str,
        *,
        leaf_size: int = 32,
        fanout: int = 8,
        reducer: Callable[[list[SessionEvent]], str] | None = None,
        force: bool = False,
    ) -> str | None:
        reducer = reducer or self._deterministic_summary
        events = self.events(session_id, limit=10_000_000)
        if not events:
            return None
        if not force:
            with self.state.read() as db:
                existing = db.execute(
                    "SELECT summary_id,source_end FROM session_summaries WHERE session_id=? AND invalidated_at IS NULL ORDER BY source_end DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            if existing and int(existing["source_end"]) >= events[-1].sequence:
                return existing["summary_id"]
        level_nodes: list[tuple[str, int, int, str]] = []
        with self.state.transaction(immediate=True) as db:
            for offset in range(0, len(events), max(1, leaf_size)):
                group = events[offset: offset + leaf_size]
                content = reducer(group)
                source_hash = sha256_bytes(canonical_json([event.event_hash for event in group]))
                summary_id = "sum-" + sha256_bytes(canonical_json({"session": session_id, "start": group[0].sequence, "end": group[-1].sequence, "hash": source_hash, "level": 0}))[:32]
                db.execute(
                    "INSERT OR REPLACE INTO session_summaries(summary_id,session_id,content,source_start,source_end,child_ids_json,source_hash,order_level,created_at,invalidated_at) VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                    (summary_id, session_id, content, group[0].sequence, group[-1].sequence, "[]", source_hash, 0, time.time()),
                )
                level_nodes.append((summary_id, group[0].sequence, group[-1].sequence, source_hash))
            level = 1
            while len(level_nodes) > 1:
                next_nodes: list[tuple[str, int, int, str]] = []
                for offset in range(0, len(level_nodes), max(2, fanout)):
                    group = level_nodes[offset: offset + max(2, fanout)]
                    child_ids = [row[0] for row in group]
                    contents = [
                        db.execute("SELECT content FROM session_summaries WHERE summary_id=?", (child_id,)).fetchone()[0]
                        for child_id in child_ids
                    ]
                    content = f"Summary level {level}; coverage {group[0][1]}-{group[-1][2]}\n" + "\n---\n".join(contents)
                    source_hash = sha256_bytes(canonical_json([row[3] for row in group]))
                    summary_id = "sum-" + sha256_bytes(canonical_json({"session": session_id, "children": child_ids, "hash": source_hash, "level": level}))[:32]
                    db.execute(
                        "INSERT OR REPLACE INTO session_summaries(summary_id,session_id,content,source_start,source_end,child_ids_json,source_hash,order_level,created_at,invalidated_at) VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                        (summary_id, session_id, content, group[0][1], group[-1][2], json.dumps(child_ids), source_hash, level, time.time()),
                    )
                    next_nodes.append((summary_id, group[0][1], group[-1][2], source_hash))
                level_nodes = next_nodes
                level += 1
        return level_nodes[0][0]

    def expand_summary(self, summary_id: str) -> dict[str, Any]:
        with self.state.read() as db:
            row = db.execute("SELECT * FROM session_summaries WHERE summary_id=?", (summary_id,)).fetchone()
            if not row:
                raise KeyError(summary_id)
            if row["invalidated_at"] is not None:
                raise ValueError("summary is invalidated")
            events = db.execute(
                "SELECT * FROM session_events WHERE session_id=? AND sequence BETWEEN ? AND ? ORDER BY sequence",
                (row["session_id"], row["source_start"], row["source_end"]),
            ).fetchall()
        values = [
            SessionEvent(item["session_id"], int(item["sequence"]), item["event_type"], json.loads(item["payload_json"]), item["previous_hash"], item["event_hash"], float(item["created_at"]))
            for item in events
        ]
        source_hash = sha256_bytes(canonical_json([event.event_hash for event in values]))
        # Root source hashes are hierarchical; leaf hashes match exact event sets.
        return {
            "summary_id": summary_id,
            "session_id": row["session_id"],
            "coverage": len(values),
            "range": [row["source_start"], row["source_end"]],
            "events": [asdict(event) for event in values],
            "event_set_hash": source_hash,
            "content": row["content"],
        }

    def active_context(
        self,
        session_id: str,
        *,
        token_budget: int = 32_000,
        recent_events: int = 24,
        chars_per_token: float = 4.0,
    ) -> dict[str, Any]:
        events = self.events(session_id, limit=10_000_000)
        root = self.compact(session_id) if len(events) > recent_events else None
        selected_events = events[-recent_events:]
        sections: list[dict[str, Any]] = []
        if root:
            with self.state.read() as db:
                row = db.execute("SELECT content,source_start,source_end FROM session_summaries WHERE summary_id=?", (root,)).fetchone()
            sections.append({"role": "summary", "id": root, "text": row["content"], "range": [row["source_start"], row["source_end"]]})
        for event in selected_events:
            sections.append({"role": "event", "id": f"event:{event.sequence}", "text": json.dumps({"type": event.event_type, "payload": event.payload}, ensure_ascii=False, sort_keys=True)})
        used = 0
        selected: list[dict[str, Any]] = []
        for section in reversed(sections):
            tokens = max(1, int(len(section["text"]) / chars_per_token) + 1)
            if used + tokens > token_budget:
                continue
            selected.append({**section, "estimated_tokens": tokens})
            used += tokens
        selected.reverse()
        return {
            "session_id": session_id,
            "budget": token_budget,
            "used": used,
            "sections": selected,
            "root_summary_id": root,
            "recent_event_count": len(selected_events),
            "exact_history_events": len(events),
        }

    def checkpoint(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> SessionCheckpoint:
        verification = self.verify(session_id)
        if not verification["ok"]:
            raise ValueError("session history failed verification")
        root = self.compact(session_id, force=True)
        through = int(verification["events"])
        checkpoint_id = "cp-" + uuid.uuid4().hex
        now = time.time()
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO session_checkpoints(checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (checkpoint_id, session_id, through, root, verification["last_hash"], json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), now),
            )
        return SessionCheckpoint(checkpoint_id, session_id, through, root, verification["last_hash"], now, metadata or {})

    def fork(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> SessionRecord:
        checkpoint = self.checkpoint(session_id, metadata={"reason": "fork-source"})
        child = self.create_session(parent_ids=(session_id,), metadata={"fork_checkpoint": checkpoint.checkpoint_id, **(metadata or {})})
        self.append(child.session_id, "session-fork", {"parent_session": session_id, "checkpoint": checkpoint.checkpoint_id, "through_sequence": checkpoint.through_sequence})
        return child

    def merge(self, session_ids: Iterable[str], *, metadata: dict[str, Any] | None = None) -> SessionRecord:
        parents = tuple(dict.fromkeys(session_ids))
        if len(parents) < 2:
            raise ValueError("merge requires at least two sessions")
        checkpoints = [self.checkpoint(session_id, metadata={"reason": "merge-source"}) for session_id in parents]
        merged = self.create_session(parent_ids=parents, metadata={"merge_checkpoints": [item.checkpoint_id for item in checkpoints], **(metadata or {})})
        self.append(merged.session_id, "session-merge", {"parents": list(parents), "checkpoints": [item.checkpoint_id for item in checkpoints]})
        return merged

    def close(self, session_id: str) -> SessionRecord:
        self.checkpoint(session_id, metadata={"reason": "close"})
        with self.state.transaction(immediate=True) as db:
            db.execute("UPDATE sessions SET state='CLOSED',updated_at=? WHERE session_id=?", (time.time(), session_id))
        return self.get_session(session_id)

    def export(self, session_id: str, path: Path) -> dict[str, Any]:
        session = self.get_session(session_id)
        events = self.events(session_id, limit=10_000_000)
        checkpoints = []
        with self.state.read() as db:
            for row in db.execute("SELECT * FROM session_checkpoints WHERE session_id=? ORDER BY created_at", (session_id,)):
                checkpoints.append({**dict(row), "metadata": json.loads(row["metadata_json"])})
        payload = {
            "schema_version": 1,
            "project_id": self.project_id,
            "session": asdict(session),
            "events": [asdict(event) for event in events],
            "checkpoints": checkpoints,
            "verification": self.verify(session_id),
        }
        payload["export_hash"] = sha256_bytes(canonical_json(payload))
        atomic_write_json(path, payload, mode=0o600)
        return {"path": str(path), "events": len(events), "hash": payload["export_hash"]}

    def import_session(self, path: Path, *, new_session_id: str | None = None) -> SessionRecord:
        value = json.loads(path.read_text(encoding="utf-8"))
        saved_hash = value.pop("export_hash", None)
        if saved_hash != sha256_bytes(canonical_json(value)):
            raise ValueError("session export hash mismatch")
        source = value["session"]
        session = self.create_session(
            session_id=new_session_id,
            parent_ids=(),
            metadata={"imported_from": source["session_id"], **source.get("metadata", {})},
        )
        for row in value.get("events", []):
            event = self.append(session.session_id, row["event_type"], row["payload"])
            if event.event_hash != row["event_hash"] and new_session_id is None:
                # Session id contributes to the hash. Preserve exact identity only when reusing the source id.
                self.quarantine(session.session_id, "event", str(row["sequence"]), "import-hash-changed", row)
        return session

    def quarantine(self, session_id: str, object_type: str, object_id: str, reason: str, payload: dict[str, Any]) -> int:
        with self.state.transaction(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO session_quarantine(session_id,object_type,object_id,reason,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                (session_id, object_type, object_id, reason, json.dumps(payload, ensure_ascii=False, sort_keys=True), time.time()),
            )
        return int(cursor.lastrowid)

    def recover(self) -> dict[str, Any]:
        integrity = self.state.integrity_check()
        sessions = self.list_sessions()
        results = {session.session_id: self.verify(session.session_id) for session in sessions}
        return {"ok": integrity and all(result["ok"] for result in results.values()), "database_integrity": integrity, "sessions": results}

    def compact_due(self, *, min_events: int = 64) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for session in self.list_sessions(state="ACTIVE"):
            events = self.events(session.session_id, limit=10_000_000)
            if len(events) < min_events:
                continue
            root = self.compact(session.session_id)
            compacted.append({"session_id": session.session_id, "events": len(events), "root_summary_id": root})
        return compacted

    def start_compactor(self, *, interval_seconds: float = 5.0, min_events: int = 64) -> threading.Thread:
        if self._worker and self._worker.is_alive():
            return self._worker
        self._stop.clear()

        def worker() -> None:
            while not self._stop.wait(max(0.1, interval_seconds)):
                try:
                    self.compact_due(min_events=min_events)
                except Exception:
                    # Background compaction must never corrupt or block the foreground event path.
                    continue

        self._worker = threading.Thread(target=worker, name="signalcore-session-compactor", daemon=True)
        self._worker.start()
        return self._worker

    def stop_compactor(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=timeout)
