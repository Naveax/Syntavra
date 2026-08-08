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

from syntavra_runtime.job_scheduler import DurableJobScheduler, JobSpec

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
    scheduler = DurableJobScheduler(path)
    for job_id, attempt, max_attempts, lease_until in [
        ("retry-job", 1, 3, time.time() - 1),
        ("dead-job", 3, 3, time.time() - 1),
        ("active-job", 1, 3, time.time() + 3600),
    ]:
        scheduler.submit(
            JobSpec(
                project_id="project-r38",
                argv=("python", "-V"),
                max_attempts=max_attempts,
                metadata={"fixture": job_id},
            ),
            job_id=job_id,
        )
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE scheduled_jobs SET state='running',attempt=?,lease_owner=?,lease_until=? "
                "WHERE job_id=?",
                (attempt, f"worker-{job_id}", lease_until, job_id),
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
            "SELECT job_id,state,lease_owner,lease_until,attempt,max_attempts,last_error "
            "FROM scheduled_jobs ORDER BY job_id"
        ):
            jobs[str(row["job_id"])] = {
                "state": row["state"],
                "lease_owner": row["lease_owner"],
                "lease_until": row["lease_until"],
                "attempt": row["attempt"],
                "max_attempts": row["max_attempts"],
                "last_error": row["last_error"],
            }
        events = [
            {
                "job_id": row["job_id"],
                "event": row["event"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in connection.execute(
                "SELECT job_id,event,payload_json FROM scheduler_events "
                "WHERE event='lease-expired' ORDER BY sequence"
            )
        ]
        return {"jobs": jobs, "events": events}
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
    assert jobs["retry-job"] == {
        "state": "queued",
        "lease_owner": "",
        "lease_until": 0.0,
        "attempt": 1,
        "max_attempts": 3,
        "last_error": "lease-expired",
    }
    assert jobs["dead-job"]["state"] == "dead-letter"
    assert jobs["dead-job"]["last_error"] == "lease-expired"
    assert jobs["active-job"]["state"] == "running"
    assert jobs["active-job"]["lease_owner"] == "worker-active-job"
    assert rust_snapshot["events"] == [
        {
            "job_id": "retry-job",
            "event": "lease-expired",
            "payload": {"next_state": "queued"},
        },
        {
            "job_id": "dead-job",
            "event": "lease-expired",
            "payload": {"next_state": "dead-letter"},
        },
    ]
