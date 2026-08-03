from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.job_scheduler import DurableJobScheduler

ROOT = Path(__file__).resolve().parents[2]


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bins"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    selector = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert selector.is_file(), selector
    return selector


def _python_reap(state_root: Path) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--state-root",
            str(state_root),
            "scheduler",
            "reap",
        ]
    )


def _rust_reap(state_root: Path) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--state-root",
            str(state_root),
            "scheduler",
            "reap",
        ]
    )


def _seed(path: Path) -> None:
    DurableJobScheduler(path)
    now = time.time()
    connection = sqlite3.connect(path)
    try:
        rows = [
            (
                "retry-job",
                "test",
                "{}",
                "running",
                5,
                now - 20,
                now - 20,
                now - 10,
                None,
                now - 1,
                "worker-a",
                1,
                3,
                2.0,
                None,
                None,
                None,
                "{}",
            ),
            (
                "dead-job",
                "test",
                "{}",
                "running",
                4,
                now - 30,
                now - 30,
                now - 20,
                None,
                now - 1,
                "worker-b",
                3,
                3,
                4.0,
                None,
                None,
                None,
                "{}",
            ),
            (
                "active-job",
                "test",
                "{}",
                "running",
                3,
                now - 10,
                now - 10,
                now - 5,
                None,
                now + 3600,
                "worker-c",
                1,
                3,
                1.0,
                None,
                None,
                None,
                "{}",
            ),
        ]
        connection.executemany(
            "INSERT INTO jobs("
            "job_id,kind,payload_json,state,priority,created_at,available_at,started_at,finished_at,"
            "lease_expires_at,worker_id,attempts,max_attempts,retry_backoff_seconds,idempotency_key,"
            "result_json,error,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _snapshot(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        jobs = {}
        for row in connection.execute(
            "SELECT job_id,state,lease_expires_at,worker_id,attempts,max_attempts,error,"
            "finished_at,retry_backoff_seconds,available_at FROM jobs ORDER BY job_id"
        ):
            jobs[str(row["job_id"])] = {
                "state": row["state"],
                "lease_expires_at": row["lease_expires_at"],
                "worker_id": row["worker_id"],
                "attempts": row["attempts"],
                "max_attempts": row["max_attempts"],
                "error": row["error"],
                "finished": row["finished_at"] is not None,
                "retry_backoff_seconds": row["retry_backoff_seconds"],
                "available_in_future": float(row["available_at"]) > time.time() - 1,
            }
        dead_letters = [
            dict(row)
            for row in connection.execute(
                "SELECT job_id,kind,payload_json,attempts,error,metadata_json FROM dead_letters ORDER BY dead_id"
            )
        ]
        return {"jobs": jobs, "dead_letters": dead_letters}
    finally:
        connection.close()


def test_native_empty_scheduler_reap_matches_python(tmp_path: Path) -> None:
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    assert _rust_reap(rust_state) == _python_reap(python_state) == {"reaped": 0}


def test_native_scheduler_reap_matches_python_state_transitions(tmp_path: Path) -> None:
    source = tmp_path / "source" / "scheduler.sqlite3"
    source.parent.mkdir(parents=True)
    _seed(source)
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_state.mkdir()
    rust_state.mkdir()
    shutil.copy2(source, python_state / "scheduler.sqlite3")
    shutil.copy2(source, rust_state / "scheduler.sqlite3")

    python_result = _python_reap(python_state)
    rust_result = _rust_reap(rust_state)

    assert rust_result == python_result == {"reaped": 2}
    python_snapshot = _snapshot(python_state / "scheduler.sqlite3")
    rust_snapshot = _snapshot(rust_state / "scheduler.sqlite3")
    assert rust_snapshot == python_snapshot

    jobs = rust_snapshot["jobs"]
    assert jobs["retry-job"]["state"] == "queued"
    assert jobs["retry-job"]["lease_expires_at"] is None
    assert jobs["retry-job"]["worker_id"] is None
    assert jobs["retry-job"]["error"] == "lease expired"
    assert jobs["retry-job"]["finished"] is False
    assert jobs["dead-job"]["state"] == "dead-letter"
    assert jobs["dead-job"]["finished"] is True
    assert jobs["active-job"]["state"] == "running"
    assert jobs["active-job"]["worker_id"] == "worker-c"
    assert rust_snapshot["dead_letters"] == [
        {
            "job_id": "dead-job",
            "kind": "test",
            "payload_json": "{}",
            "attempts": 3,
            "error": "lease expired",
            "metadata_json": "{}",
        }
    ]
