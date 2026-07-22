from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2


class StateDB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(9):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                last_error = None
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() and "busy" not in str(exc).casefold():
                    connection.close()
                    raise
                last_error = exc
                time.sleep(min(0.5, 0.01 * (2 ** attempt)))
        if last_error is not None:
            connection.close()
            raise last_error
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    def _initialize(self) -> None:
        with self.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    argv_json TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    pid INTEGER,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    evidence_handle TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    timeout_seconds REAL NOT NULL DEFAULT 0,
                    stdout_path TEXT NOT NULL DEFAULT '',
                    stderr_path TEXT NOT NULL DEFAULT '',
                    repository_tree TEXT NOT NULL DEFAULT 'unknown',
                    environment_hash TEXT NOT NULL DEFAULT 'unknown',
                    project_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at DESC);
                CREATE TABLE IF NOT EXISTS completion_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    exit_code INTEGER,
                    completed_at REAL NOT NULL,
                    evidence_handle TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS verifier_results(
                    cache_key TEXT PRIMARY KEY,
                    command_json TEXT NOT NULL,
                    tree_hash TEXT NOT NULL,
                    environment_hash TEXT NOT NULL,
                    dependency_hash TEXT NOT NULL,
                    toolchain_hash TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    exit_code INTEGER NOT NULL,
                    evidence_handle TEXT NOT NULL,
                    affected_paths_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            columns = self._columns(db, "jobs")
            if "project_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
            db.execute(
                "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def upsert_job(self, values: dict[str, Any]) -> None:
        columns = sorted(values)
        assignments = ",".join(f"{column}=excluded.{column}" for column in columns if column != "job_id")
        sql = (
            f"INSERT INTO jobs({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(job_id) DO UPDATE SET {assignments}"
        )
        with self.transaction(immediate=True) as db:
            db.execute(sql, tuple(values[column] for column in columns))

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        assignments = ",".join(f"{column}=?" for column in sorted(values))
        params = [values[column] for column in sorted(values)] + [job_id]
        with self.transaction(immediate=True) as db:
            cursor = db.execute(f"UPDATE jobs SET {assignments} WHERE job_id=?", params)
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self, *, states: tuple[str, ...] = (), limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if states:
            sql += f" WHERE state IN ({','.join('?' for _ in states)})"
            params.extend(states)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.read() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def record_completion(self, payload: dict[str, Any]) -> int:
        with self.transaction(immediate=True) as db:
            db.execute(
                """
                INSERT INTO completion_events(job_id,state,exit_code,completed_at,evidence_handle,payload_json)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state=excluded.state,
                    exit_code=excluded.exit_code,
                    completed_at=excluded.completed_at,
                    evidence_handle=excluded.evidence_handle,
                    payload_json=excluded.payload_json
                """,
                (
                    payload["job_id"],
                    payload["state"],
                    payload.get("exit_code"),
                    payload["completed_at"],
                    payload.get("evidence_handle", ""),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            row = db.execute("SELECT sequence FROM completion_events WHERE job_id=?", (payload["job_id"],)).fetchone()
        return int(row[0])

    def completions_after(self, sequence: int, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.read() as db:
            rows = db.execute(
                "SELECT * FROM completion_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (max(0, sequence), max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_completion(self, path: Path, payload: dict[str, Any]) -> None:
        """Compatibility JSONL queue plus authoritative SQLite completion event."""
        self.record_completion(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()

    def integrity_check(self) -> bool:
        with self.read() as db:
            return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def now(self) -> float:
        return time.time()
