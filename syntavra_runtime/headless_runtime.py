from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .execution_sandbox import NativeSandboxBroker, SandboxPolicy


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open one transactional SQLite connection and always release its file handle."""

    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class JobState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


_FINAL = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
_ALLOWED = {
    JobState.QUEUED: {JobState.CLAIMED, JobState.CANCELLED},
    JobState.CLAIMED: {JobState.RUNNING, JobState.QUEUED, JobState.CANCELLED},
    JobState.RUNNING: {JobState.VERIFYING, JobState.COMPLETED, JobState.FAILED, JobState.BLOCKED, JobState.CANCELLED},
    JobState.VERIFYING: {JobState.COMPLETED, JobState.FAILED, JobState.BLOCKED, JobState.CANCELLED},
    JobState.BLOCKED: {JobState.QUEUED, JobState.CANCELLED},
    JobState.COMPLETED: set(),
    JobState.FAILED: {JobState.QUEUED},
    JobState.CANCELLED: {JobState.QUEUED},
}


@dataclass(frozen=True)
class HeadlessJob:
    job_id: str
    state: JobState
    workspace_type: str
    workspace: str
    command: tuple[str, ...]
    policy: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    attempts: int = 0
    claimed_by: str = ""
    result: dict[str, Any] = field(default_factory=dict)


class HeadlessRuntime:
    """Durable local/CI/remote job queue with migration-safe state bundles."""

    def __init__(self, path: Path, state_root: Path, *, broker: NativeSandboxBroker | None = None):
        self.path = path
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.broker = broker or NativeSandboxBroker(self.state_root)
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    workspace_type TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    claimed_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, sequence);
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> HeadlessJob:
        return HeadlessJob(
            job_id=row["job_id"],
            state=JobState(row["state"]),
            workspace_type=row["workspace_type"],
            workspace=row["workspace"],
            command=tuple(json.loads(row["command_json"])),
            policy=json.loads(row["policy_json"]),
            metadata=json.loads(row["metadata_json"]),
            result=json.loads(row["result_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            attempts=int(row["attempts"]),
            claimed_by=row["claimed_by"],
        )

    def submit(
        self,
        command: Sequence[str],
        *,
        workspace: Path,
        workspace_type: str = "local-worktree",
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> HeadlessJob:
        if not command:
            raise ValueError("command is required")
        workspace = workspace.resolve(strict=True)
        created = _now()
        body = {
            "command": list(command),
            "workspace": str(workspace),
            "workspace_type": workspace_type,
            "policy": dict(policy or {}),
            "metadata": dict(metadata or {}),
            "created": created,
        }
        identifier = job_id or "sha256:" + hashlib.sha256(_canonical(body).encode()).hexdigest()
        with _connect(self.path) as db:
            db.execute(
                """INSERT INTO jobs
                   (job_id,state,workspace_type,workspace,command_json,policy_json,metadata_json,result_json,created_at,updated_at,attempts,claimed_by)
                   VALUES(?,?,?,?,?,?,?,?,?,?,0,'')""",
                (
                    identifier,
                    JobState.QUEUED.value,
                    workspace_type,
                    str(workspace),
                    json.dumps(list(command)),
                    _canonical(dict(policy or {})),
                    _canonical(dict(metadata or {})),
                    "{}",
                    created,
                    created,
                ),
            )
            db.execute(
                "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (identifier, "submitted", _canonical(body), created),
            )
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (identifier,)).fetchone()
        assert row is not None
        return self._record(row)

    def get(self, job_id: str) -> HeadlessJob:
        with _connect(self.path) as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def transition(
        self,
        job_id: str,
        state: JobState,
        *,
        result: Mapping[str, Any] | None = None,
        claimed_by: str | None = None,
        event: str | None = None,
    ) -> HeadlessJob:
        with _connect(self.path) as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = JobState(row["state"])
            if state not in _ALLOWED[current]:
                raise ValueError(f"invalid job transition: {current.value} -> {state.value}")
            updated = _now()
            attempts = int(row["attempts"]) + (1 if state == JobState.RUNNING else 0)
            merged_result = json.loads(row["result_json"])
            merged_result.update(dict(result or {}))
            owner = row["claimed_by"] if claimed_by is None else claimed_by
            db.execute(
                "UPDATE jobs SET state=?,result_json=?,updated_at=?,attempts=?,claimed_by=? WHERE job_id=?",
                (state.value, _canonical(merged_result), updated, attempts, owner, job_id),
            )
            db.execute(
                "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (job_id, event or state.value, _canonical({"from": current.value, "to": state.value, "result": dict(result or {})}), updated),
            )
            final = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        assert final is not None
        return self._record(final)

    def claim(self, worker: str) -> HeadlessJob | None:
        if not worker.strip():
            raise ValueError("worker identity is required")
        with _connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state=? ORDER BY created_at LIMIT 1", (JobState.QUEUED.value,)).fetchone()
            if row is None:
                return None
            updated = _now()
            db.execute(
                "UPDATE jobs SET state=?,claimed_by=?,updated_at=? WHERE job_id=? AND state=?",
                (JobState.CLAIMED.value, worker, updated, row["job_id"], JobState.QUEUED.value),
            )
            db.execute(
                "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (row["job_id"], "claimed", _canonical({"worker": worker}), updated),
            )
            final = db.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
        assert final is not None
        return self._record(final)

    def run_once(self, worker: str = "local") -> HeadlessJob | None:
        job = self.claim(worker)
        if job is None:
            return None
        self.transition(job.job_id, JobState.RUNNING, claimed_by=worker)
        policy_values = dict(job.policy)
        policy = SandboxPolicy(
            workspace=Path(job.workspace),
            writable_paths=tuple(Path(value) for value in policy_values.get("writable_paths", [])),
            network_hosts=tuple(policy_values.get("network_hosts", [])),
            timeout_seconds=float(policy_values.get("timeout_seconds", 900)),
            memory_bytes=policy_values.get("memory_bytes"),
            cpu_seconds=policy_values.get("cpu_seconds"),
            strict_native=bool(policy_values.get("strict_native", False)),
        )
        try:
            receipt = self.broker.run(job.command, policy=policy)
            result = {"execution": asdict(receipt)}
            return self.transition(job.job_id, JobState.COMPLETED if receipt.ok else JobState.FAILED, result=result)
        except Exception as error:
            return self.transition(
                job.job_id,
                JobState.FAILED,
                result={"error": f"{type(error).__name__}: {error}"},
            )

    def cancel(self, job_id: str, reason: str = "operator cancellation") -> HeadlessJob:
        job = self.get(job_id)
        if job.state in _FINAL:
            return job
        return self.transition(job_id, JobState.CANCELLED, result={"cancel_reason": reason})

    def resume(self, job_id: str) -> HeadlessJob:
        job = self.get(job_id)
        if JobState.QUEUED not in _ALLOWED[job.state]:
            raise ValueError(f"job cannot be resumed from {job.state.value}")
        return self.transition(job_id, JobState.QUEUED, claimed_by="", event="resumed")

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with _connect(self.path) as db:
            if not db.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone():
                raise KeyError(job_id)
            rows = db.execute("SELECT * FROM events WHERE job_id=? ORDER BY sequence", (job_id,)).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "job_id": row["job_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def export_bundle(self, job_id: str, destination: Path) -> dict[str, Any]:
        job = self.get(job_id)
        body = {"schema": "syntavra-headless-job", "job": asdict(job), "events": self.events(job_id)}
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        envelope = {"sha256": digest, "payload": body}
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
            json.dump(envelope, handle, ensure_ascii=False, sort_keys=True, indent=2)
            temporary = Path(handle.name)
        os.replace(temporary, destination)
        return {"ok": True, "path": str(destination), "sha256": digest, "job_id": job_id}

    def import_bundle(self, source: Path, *, workspace_override: Path | None = None) -> HeadlessJob:
        envelope = json.loads(source.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(encoded).hexdigest() != envelope.get("sha256"):
            raise ValueError("headless bundle integrity failure")
        job = payload["job"]
        workspace = workspace_override or Path(job["workspace"])
        metadata = dict(job.get("metadata", {}))
        metadata["imported_from"] = str(source)
        return self.submit(
            job["command"],
            workspace=workspace,
            workspace_type=job.get("workspace_type", "imported"),
            policy=job.get("policy", {}),
            metadata=metadata,
        )

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            rows = db.execute("SELECT state,COUNT(*) count FROM jobs GROUP BY state ORDER BY state").fetchall()
            total = int(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        return {"ok": True, "jobs": total, "states": {row["state"]: int(row["count"]) for row in rows}}


__all__ = ["HeadlessJob", "HeadlessRuntime", "JobState"]
