from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    task_id: str
    source_type: str
    source_uri: str
    source_hash: str
    project_fingerprint: str
    branch: str
    commit: str
    generation: int
    timestamp: int
    trust_class: str
    taint_class: str
    exact_fragment: str
    summary: str
    recovery_handle: str
    valid_from: int
    valid_until: int
    superseded_by: str | None
    validator_links: tuple[str, ...]


class EvidenceLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, project TEXT NOT NULL, branch TEXT NOT NULL, generation INTEGER NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL)")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def _hash(payload: str, previous_hash: str) -> str:
        return hashlib.sha256((previous_hash + "\0" + payload).encode("utf-8")).hexdigest()

    def append(self, record: EvidenceRecord) -> str:
        payload = json.dumps(asdict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as connection:
            previous = connection.execute("SELECT record_hash FROM evidence WHERE project=? AND branch=? ORDER BY generation DESC, rowid DESC LIMIT 1", (record.project_fingerprint, record.branch)).fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            record_hash = self._hash(payload, previous_hash)
            connection.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?)", (record.evidence_id, record.project_fingerprint, record.branch, record.generation, payload, previous_hash, record_hash))
            connection.commit()
        return record_hash

    def retrieve(self, *, project_fingerprint: str, branch: str, now: int | None = None, limit: int = 64) -> tuple[EvidenceRecord, ...]:
        current = int(time.time()) if now is None else int(now)
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload FROM evidence WHERE project=? AND branch=? ORDER BY generation DESC, rowid DESC LIMIT ?", (project_fingerprint, branch, limit)).fetchall()
        records = []
        for (payload,) in rows:
            value = json.loads(payload)
            value["validator_links"] = tuple(value["validator_links"])
            record = EvidenceRecord(**value)
            if record.valid_from <= current <= record.valid_until and not record.superseded_by:
                records.append(record)
        return tuple(records)

    def verify_chain(self, *, project_fingerprint: str, branch: str) -> bool:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload,previous_hash,record_hash FROM evidence WHERE project=? AND branch=? ORDER BY generation, rowid", (project_fingerprint, branch)).fetchall()
        previous = "0" * 64
        for payload, previous_hash, record_hash in rows:
            if previous_hash != previous or self._hash(payload, previous_hash) != record_hash:
                return False
            previous = record_hash
        return True

    def exact_recover(self, evidence_id: str) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return json.loads(row[0])["exact_fragment"]

    def detect_contradictions(self, records: Iterable[EvidenceRecord]) -> tuple[tuple[str, str], ...]:
        by_uri: dict[str, list[EvidenceRecord]] = {}
        for record in records:
            by_uri.setdefault(record.source_uri, []).append(record)
        contradictions = []
        for items in by_uri.values():
            hashes = {item.source_hash for item in items}
            if len(hashes) > 1:
                contradictions.append((items[0].source_uri, ",".join(sorted(hashes))))
        return tuple(contradictions)
