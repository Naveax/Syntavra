from .platform_common import *


class SessionMemory:
    """Exact event chain plus multi-view summary DAG and branch operations."""

    VIEWS = (
        "task",
        "decision",
        "change",
        "failure",
        "security",
        "dependency",
        "repository",
        "test",
        "provider",
        "handoff",
    )

    def __init__(self, path: Path, *, project_id: str):
        self.path = path
        self.project_id = project_id
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL,
                    parents_json TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS summaries (
                    summary_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, view TEXT NOT NULL,
                    source_sequences_json TEXT NOT NULL, parent_summaries_json TEXT NOT NULL,
                    summary_text TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_summary_session_view ON summaries(session_id, view, created_at);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, label TEXT NOT NULL,
                    sequence INTEGER NOT NULL, event_hash TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )

    def open(self, session_id: str | None = None, *, parents: Sequence[str] = (), metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        session_id = session_id or secrets.token_hex(12)
        now = _now()
        with _connect(self.path) as db:
            existing = db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if existing:
                return dict(existing) | {"parents": json.loads(existing["parents_json"]), "metadata": json.loads(existing["metadata_json"]), "restored": True}
            for parent in parents:
                if not db.execute("SELECT 1 FROM sessions WHERE session_id = ?", (parent,)).fetchone():
                    raise KeyError(f"parent session not found: {parent}")
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                (session_id, self.project_id, "ACTIVE", json.dumps(list(parents)), json.dumps(dict(metadata or {}), sort_keys=True), now, now),
            )
        return {"session_id": session_id, "project_id": self.project_id, "state": "ACTIVE", "parents": list(parents), "metadata": dict(metadata or {}), "created_at": now, "updated_at": now, "restored": False}

    def append(self, session_id: str, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with _connect(self.path) as db:
            session = db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if not session:
                raise KeyError(session_id)
            last = db.execute("SELECT sequence,event_hash FROM events WHERE session_id = ? ORDER BY sequence DESC LIMIT 1", (session_id,)).fetchone()
            sequence = int(last["sequence"] + 1) if last else 1
            previous = last["event_hash"] if last else "0" * 64
            body = {"session_id": session_id, "sequence": sequence, "event_type": event_type, "payload": dict(payload), "previous_hash": previous}
            digest = sha256_bytes(canonical_json(body))
            created = _now()
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                (session_id, sequence, event_type, canonical_json(dict(payload)).decode("utf-8"), previous, digest, created),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (created, session_id))
        return body | {"event_hash": digest, "created_at": created}

    def events(self, session_id: str) -> list[dict[str, Any]]:
        with _connect(self.path) as db:
            rows = db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    @staticmethod
    def _summary(view: str, events: Sequence[dict[str, Any]]) -> str:
        view_terms = {
            "task": ("task", "goal", "request", "plan"),
            "decision": ("decision", "decide", "chosen", "keep", "revert", "supersede"),
            "change": ("patch", "edit", "change", "file", "commit", "diff"),
            "failure": ("fail", "error", "exception", "test", "panic", "timeout"),
            "security": ("security", "authorization", "policy", "secret", "sandbox", "capability"),
            "dependency": ("dependency", "import", "package", "provider", "adapter"),
            "repository": ("repository", "branch", "commit", "symbol", "module", "worktree"),
            "test": ("test", "verify", "coverage", "assert", "benchmark"),
            "provider": ("provider", "model", "token", "cost", "receipt", "cache"),
            "handoff": ("handoff", "agent", "resume", "migration", "fork", "merge"),
        }[view]
        selected: list[str] = []
        for event in events:
            rendered = json.dumps(event["payload"], ensure_ascii=False, sort_keys=True)
            corpus = f"{event['event_type']} {rendered}".casefold()
            if any(term in corpus for term in view_terms):
                selected.append(f"#{event['sequence']} {event['event_type']}: {rendered[:700]}")
        return "\n".join(selected[-100:]) or f"No {view} events in selected range."

    def compact(self, session_id: str, *, views: Sequence[str] | None = None) -> dict[str, Any]:
        events = self.events(session_id)
        views = tuple(views or self.VIEWS)
        unknown = sorted(set(views) - set(self.VIEWS))
        if unknown:
            raise ValueError(f"unsupported summary views: {unknown}")
        created: list[dict[str, Any]] = []
        with _connect(self.path) as db:
            for view in views:
                text = self._summary(view, events)
                source_sequences = [event["sequence"] for event in events]
                parents = [row["summary_id"] for row in db.execute("SELECT summary_id FROM summaries WHERE session_id = ? AND view = ? ORDER BY created_at DESC LIMIT 1", (session_id, view))]
                body = {"session_id": session_id, "view": view, "source_sequences": source_sequences, "parents": parents, "summary": text}
                summary_id = sha256_bytes(canonical_json(body))
                db.execute(
                    "INSERT OR IGNORE INTO summaries VALUES(?,?,?,?,?,?,?)",
                    (summary_id, session_id, view, json.dumps(source_sequences), json.dumps(parents), text, _now()),
                )
                created.append({"summary_id": summary_id, **body})
        return {"ok": True, "session_id": session_id, "events": len(events), "summaries": created, "exact_history_preserved": self.verify(session_id)["ok"]}

    @staticmethod
    def _payload_weight(payload: Mapping[str, Any]) -> float:
        importance = float(payload.get("importance", 0.0) or 0.0)
        pinned = 15.0 if payload.get("pinned") else 0.0
        stale = 35.0 if payload.get("stale") or payload.get("reverted") or payload.get("superseded") else 0.0
        return max(-40.0, min(30.0, importance * 10.0 + pinned - stale))

    def retrieve(self, session_id: str, query: str, *, limit: int = 12) -> dict[str, Any]:
        query_terms = _tokens(query)
        candidates: list[tuple[float, dict[str, Any]]] = []
        events = self.events(session_id)
        total = max(1, len(events))
        for event in events:
            payload = event["payload"]
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            matched = query_terms & _tokens(f"{event['event_type']} {rendered}")
            if query_terms and not matched:
                continue
            relevance = (len(matched) / max(1, len(query_terms))) * 65
            recency = (event["sequence"] / total) * 15
            weight = self._payload_weight(payload)
            score = relevance + recency + weight
            candidates.append((score, {"type": "event", "score": score, "sequence": event["sequence"], "event_type": event["event_type"], "payload": payload, "event_hash": event["event_hash"]}))
        with _connect(self.path) as db:
            summaries = db.execute("SELECT * FROM summaries WHERE session_id = ?", (session_id,)).fetchall()
        for row in summaries:
            matched = query_terms & _tokens(f"{row['view']} {row['summary_text']}")
            if query_terms and not matched:
                continue
            score = (len(matched) / max(1, len(query_terms))) * 70 + 8
            candidates.append((score, {"type": "summary", "score": score, "summary_id": row["summary_id"], "view": row["view"], "summary": row["summary_text"], "source_sequences": json.loads(row["source_sequences_json"])}))
        results = [row for _, row in sorted(candidates, key=lambda item: (-item[0], str(item[1].get("sequence", item[1].get("summary_id", "")))))[:max(1, limit)]]
        return {"session_id": session_id, "query": query, "results": results, "exact_recovery": self.verify(session_id)["ok"]}

    def checkpoint(self, session_id: str, label: str = "") -> dict[str, Any]:
        with _connect(self.path) as db:
            last = db.execute("SELECT sequence,event_hash FROM events WHERE session_id = ? ORDER BY sequence DESC LIMIT 1", (session_id,)).fetchone()
            sequence = int(last["sequence"]) if last else 0
            event_hash = last["event_hash"] if last else "0" * 64
            body = {"session_id": session_id, "sequence": sequence, "event_hash": event_hash, "label": label}
            checkpoint_id = sha256_bytes(canonical_json(body))
            created = _now()
            db.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?,?)", (checkpoint_id, session_id, label, sequence, event_hash, created))
        return {"checkpoint_id": checkpoint_id, **body, "created_at": created}

    def fork(self, session_id: str, *, label: str = "") -> dict[str, Any]:
        checkpoint = self.checkpoint(session_id, label or "fork")
        child = self.open(parents=(session_id,), metadata={"fork_checkpoint": checkpoint["checkpoint_id"], "label": label})
        return {"parent": session_id, "child": child, "checkpoint": checkpoint}

    def merge(self, session_ids: Sequence[str], *, label: str = "") -> dict[str, Any]:
        if len(set(session_ids)) < 2:
            raise ValueError("merge requires at least two distinct sessions")
        checkpoints = [self.checkpoint(session_id, label or "merge") for session_id in session_ids]
        merged = self.open(parents=tuple(session_ids), metadata={"merge_checkpoints": [item["checkpoint_id"] for item in checkpoints], "label": label})
        self.append(merged["session_id"], "merge", {"parents": list(session_ids), "checkpoints": checkpoints})
        return {"merged": merged, "parents": list(session_ids), "checkpoints": checkpoints}

    def restore(self, checkpoint_id: str) -> dict[str, Any]:
        with _connect(self.path) as db:
            checkpoint = db.execute("SELECT * FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)).fetchone()
        if not checkpoint:
            raise KeyError(checkpoint_id)
        events = [event for event in self.events(checkpoint["session_id"]) if event["sequence"] <= checkpoint["sequence"]]
        valid = (events[-1]["event_hash"] if events else "0" * 64) == checkpoint["event_hash"]
        return {"ok": valid, "checkpoint": dict(checkpoint), "events": events, "exact_recovery": valid}

    def verify(self, session_id: str) -> dict[str, Any]:
        previous = "0" * 64
        failures: list[str] = []
        events = self.events(session_id)
        for event in events:
            body = {"session_id": session_id, "sequence": event["sequence"], "event_type": event["event_type"], "payload": event["payload"], "previous_hash": previous}
            digest = sha256_bytes(canonical_json(body))
            if event["previous_hash"] != previous:
                failures.append(f"previous:{event['sequence']}")
            if event["event_hash"] != digest:
                failures.append(f"hash:{event['sequence']}")
            previous = event["event_hash"]
        return {"ok": not failures, "session_id": session_id, "events": len(events), "last_hash": previous, "failures": failures}

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            return {
                "sessions": int(db.execute("SELECT COUNT(*) value FROM sessions").fetchone()["value"]),
                "events": int(db.execute("SELECT COUNT(*) value FROM events").fetchone()["value"]),
                "summaries": int(db.execute("SELECT COUNT(*) value FROM summaries").fetchone()["value"]),
                "checkpoints": int(db.execute("SELECT COUNT(*) value FROM checkpoints").fetchone()["value"]),
                "views": list(self.VIEWS),
            }
