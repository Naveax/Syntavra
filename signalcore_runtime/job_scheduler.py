from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .state import StateDB


class SchedulerError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobSpec:
    project_id: str
    argv: tuple[str, ...]
    priority: int = 0
    dependencies: tuple[str, ...] = ()
    max_attempts: int = 3
    timeout_seconds: float = 1200.0
    sandbox_profile: str = "strict"
    resource_class: str = "cpu"
    metadata: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if not self.project_id or not self.argv:
            raise SchedulerError("project_id and argv are required")
        if not -100 <= self.priority <= 100:
            raise SchedulerError("priority must be between -100 and 100")
        if self.max_attempts < 1 or self.timeout_seconds <= 0:
            raise SchedulerError("invalid retry/timeout policy")


@dataclass(frozen=True)
class JobLease:
    job_id: str
    project_id: str
    argv: tuple[str, ...]
    attempt: int
    lease_owner: str
    lease_until: float
    timeout_seconds: float
    sandbox_profile: str
    resource_class: str
    metadata: dict[str, Any]


class DurableJobScheduler:
    """SQLite-backed priority/fairness/dependency scheduler with leases and DLQ."""

    def __init__(self, path: Path):
        self.state = StateDB(path)
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs(
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    argv_json TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    timeout_seconds REAL NOT NULL,
                    sandbox_profile TEXT NOT NULL,
                    resource_class TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    scheduled_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS scheduled_jobs_ready_idx
                    ON scheduled_jobs(state,scheduled_at,priority,created_at);
                CREATE INDEX IF NOT EXISTS scheduled_jobs_project_idx
                    ON scheduled_jobs(project_id,state);
                CREATE TABLE IF NOT EXISTS job_dependencies(
                    job_id TEXT NOT NULL,
                    dependency_id TEXT NOT NULL,
                    PRIMARY KEY(job_id,dependency_id),
                    FOREIGN KEY(job_id) REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS scheduler_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    def submit(self, spec: JobSpec, *, job_id: str = "", scheduled_at: float | None = None) -> str:
        spec.validate()
        identifier = job_id or "job-" + uuid.uuid4().hex
        now = time.time()
        if identifier in spec.dependencies:
            raise SchedulerError("job cannot depend on itself")
        with self.state.transaction(immediate=True) as db:
            for dependency in spec.dependencies:
                if db.execute("SELECT 1 FROM scheduled_jobs WHERE job_id=?", (dependency,)).fetchone() is None:
                    raise SchedulerError(f"unknown dependency: {dependency}")
            db.execute(
                "INSERT INTO scheduled_jobs(job_id,project_id,argv_json,priority,state,attempt,max_attempts,timeout_seconds,sandbox_profile,resource_class,metadata_json,scheduled_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier, spec.project_id, json.dumps(spec.argv), spec.priority, "queued", 0,
                    spec.max_attempts, spec.timeout_seconds, spec.sandbox_profile, spec.resource_class,
                    json.dumps(dict(spec.metadata or {}), sort_keys=True), float(scheduled_at or now), now, now,
                ),
            )
            db.executemany(
                "INSERT INTO job_dependencies(job_id,dependency_id) VALUES(?,?)",
                ((identifier, dependency) for dependency in spec.dependencies),
            )
            self._event(db, identifier, "submitted", {"priority": spec.priority})
        return identifier

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
        project_concurrency: int = 2,
        resource_classes: Iterable[str] = (),
        now: float | None = None,
    ) -> JobLease | None:
        if not worker_id or lease_seconds <= 0 or project_concurrency < 1:
            raise SchedulerError("invalid lease request")
        current = time.time() if now is None else float(now)
        classes = tuple(str(item) for item in resource_classes)
        with self.state.transaction(immediate=True) as db:
            self._reap_expired(db, current)
            query = """
                SELECT j.* FROM scheduled_jobs j
                WHERE j.state='queued' AND j.scheduled_at<=?
                  AND NOT EXISTS(
                    SELECT 1 FROM job_dependencies d
                    JOIN scheduled_jobs dependency ON dependency.job_id=d.dependency_id
                    WHERE d.job_id=j.job_id AND dependency.state!='succeeded'
                  )
                  AND (
                    SELECT COUNT(*) FROM scheduled_jobs active
                    WHERE active.project_id=j.project_id AND active.state='running'
                  ) < ?
            """
            params: list[Any] = [current, project_concurrency]
            if classes:
                query += " AND j.resource_class IN (" + ",".join("?" for _ in classes) + ")"
                params.extend(classes)
            # Priority first, then least recently serviced project, then FIFO.
            query += """
                ORDER BY j.priority DESC,
                    COALESCE((SELECT MAX(updated_at) FROM scheduled_jobs p WHERE p.project_id=j.project_id AND p.state IN ('running','succeeded','failed')),0) ASC,
                    j.created_at ASC LIMIT 1
            """
            row = db.execute(query, tuple(params)).fetchone()
            if row is None:
                return None
            attempt = int(row["attempt"]) + 1
            lease_until = current + lease_seconds
            updated = db.execute(
                "UPDATE scheduled_jobs SET state='running',attempt=?,lease_owner=?,lease_until=?,updated_at=? WHERE job_id=? AND state='queued'",
                (attempt, worker_id, lease_until, current, row["job_id"]),
            )
            if updated.rowcount != 1:
                return None
            self._event(db, str(row["job_id"]), "claimed", {"worker": worker_id, "attempt": attempt})
            return JobLease(
                str(row["job_id"]), str(row["project_id"]), tuple(json.loads(row["argv_json"])), attempt,
                worker_id, lease_until, float(row["timeout_seconds"]), str(row["sandbox_profile"]),
                str(row["resource_class"]), dict(json.loads(row["metadata_json"])),
            )

    def heartbeat(self, job_id: str, worker_id: str, *, lease_seconds: float = 60.0) -> float:
        now = time.time()
        until = now + lease_seconds
        with self.state.transaction(immediate=True) as db:
            updated = db.execute(
                "UPDATE scheduled_jobs SET lease_until=?,updated_at=? WHERE job_id=? AND state='running' AND lease_owner=?",
                (until, now, job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise SchedulerError("job lease is not owned by worker")
            self._event(db, job_id, "heartbeat", {"worker": worker_id, "lease_until": until})
        return until

    def complete(self, job_id: str, worker_id: str, result: Mapping[str, Any] | None = None) -> None:
        with self.state.transaction(immediate=True) as db:
            updated = db.execute(
                "UPDATE scheduled_jobs SET state='succeeded',result_json=?,lease_owner='',lease_until=0,updated_at=? WHERE job_id=? AND state='running' AND lease_owner=?",
                (json.dumps(dict(result or {}), sort_keys=True), time.time(), job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise SchedulerError("job lease is not owned by worker")
            self._event(db, job_id, "succeeded", dict(result or {}))

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        base_backoff_seconds: float = 2.0,
    ) -> str:
        now = time.time()
        with self.state.transaction(immediate=True) as db:
            row = db.execute(
                "SELECT attempt,max_attempts FROM scheduled_jobs WHERE job_id=? AND state='running' AND lease_owner=?",
                (job_id, worker_id),
            ).fetchone()
            if row is None:
                raise SchedulerError("job lease is not owned by worker")
            attempt = int(row["attempt"])
            if retryable and attempt < int(row["max_attempts"]):
                delay = min(3600.0, base_backoff_seconds * (2 ** max(0, attempt - 1)))
                state = "queued"
                scheduled = now + delay
                event = "retry-scheduled"
            else:
                state = "dead-letter" if attempt >= int(row["max_attempts"]) else "failed"
                scheduled = now
                event = state
            db.execute(
                "UPDATE scheduled_jobs SET state=?,scheduled_at=?,last_error=?,lease_owner='',lease_until=0,updated_at=? WHERE job_id=?",
                (state, scheduled, error[:4096], now, job_id),
            )
            self._event(db, job_id, event, {"attempt": attempt, "error": error[:512]})
            return state

    def cancel(self, job_id: str) -> bool:
        with self.state.transaction(immediate=True) as db:
            updated = db.execute(
                "UPDATE scheduled_jobs SET state='cancelled',lease_owner='',lease_until=0,updated_at=? WHERE job_id=? AND state IN ('queued','running')",
                (time.time(), job_id),
            )
            if updated.rowcount:
                self._event(db, job_id, "cancelled", {})
            return bool(updated.rowcount)

    def list(self, *, states: Iterable[str] = (), limit: int = 100) -> list[dict[str, Any]]:
        selected = tuple(str(item) for item in states)
        with self.state.read() as db:
            query = "SELECT * FROM scheduled_jobs"
            params: list[Any] = []
            if selected:
                query += " WHERE state IN (" + ",".join("?" for _ in selected) + ")"
                params.extend(selected)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 1000)))
            return [dict(row) for row in db.execute(query, tuple(params))]

    def events(self, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        with self.state.read() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM scheduler_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, max(1, min(limit, 1000))),
            )]
        return {"events": rows, "cursor": rows[-1]["sequence"] if rows else after}

    def reap(self) -> int:
        with self.state.transaction(immediate=True) as db:
            return self._reap_expired(db, time.time())

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, event: str, payload: Mapping[str, Any]) -> None:
        db.execute(
            "INSERT INTO scheduler_events(job_id,event,payload_json,created_at) VALUES(?,?,?,?)",
            (job_id, event, json.dumps(dict(payload), sort_keys=True), time.time()),
        )

    def _reap_expired(self, db: sqlite3.Connection, now: float) -> int:
        rows = list(db.execute("SELECT job_id,attempt,max_attempts FROM scheduled_jobs WHERE state='running' AND lease_until>0 AND lease_until<=?", (now,)))
        for row in rows:
            state = "queued" if int(row["attempt"]) < int(row["max_attempts"]) else "dead-letter"
            db.execute(
                "UPDATE scheduled_jobs SET state=?,lease_owner='',lease_until=0,scheduled_at=?,last_error='lease-expired',updated_at=? WHERE job_id=?",
                (state, now, now, row["job_id"]),
            )
            self._event(db, str(row["job_id"]), "lease-expired", {"next_state": state})
        return len(rows)

    def stats(self) -> dict[str, Any]:
        with self.state.read() as db:
            states = {str(row[0]): int(row[1]) for row in db.execute("SELECT state,COUNT(*) FROM scheduled_jobs GROUP BY state")}
            projects = int(db.execute("SELECT COUNT(DISTINCT project_id) FROM scheduled_jobs").fetchone()[0])
        return {"states": states, "projects": projects, "database_integrity": self.state.integrity_check()}
