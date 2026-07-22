from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: str
    memory_class: str
    project_fingerprint: str
    branch: str
    commit: str
    source_hashes: tuple[str, ...]
    valid_from: int
    valid_until: int
    confidence: float
    superseded_by: str | None
    invalidation_rule: str
    content: str
    trust: str


class ScopedMemory:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS memory(id TEXT PRIMARY KEY, project TEXT NOT NULL, branch TEXT NOT NULL, commit_id TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.commit()

    def put(self, item: MemoryItem) -> None:
        if item.memory_class not in {"episodic", "semantic", "procedural", "decision", "operational"}:
            raise ValueError("unknown memory class")
        payload = json.dumps(asdict(item), sort_keys=True, ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT OR REPLACE INTO memory VALUES(?,?,?,?,?)", (item.memory_id, item.project_fingerprint, item.branch, item.commit, payload))
            connection.commit()

    def query(self, *, project_fingerprint: str, branch: str, commit: str, now: int | None = None) -> tuple[MemoryItem, ...]:
        current = int(time.time()) if now is None else int(now)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT payload FROM memory WHERE project=? AND branch=? AND commit_id=?", (project_fingerprint, branch, commit)).fetchall()
        result = []
        for (payload,) in rows:
            value = json.loads(payload)
            value["source_hashes"] = tuple(value["source_hashes"])
            item = MemoryItem(**value)
            if item.valid_from <= current <= item.valid_until and not item.superseded_by:
                result.append(item)
        return tuple(result)
