from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


def _run(engine: str, project: Path, state_root: Path, *arguments: str) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state_root),
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _initialize(state_root: Path) -> sqlite3.Connection:
    broker = state_root / "broker"
    broker.mkdir(parents=True)
    database = sqlite3.connect(broker / "broker.sqlite3")
    database.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE jobs(
          job_id TEXT PRIMARY KEY,state TEXT NOT NULL,argv_json TEXT NOT NULL,cwd TEXT NOT NULL,
          created_at REAL NOT NULL,started_at REAL,completed_at REAL,pid INTEGER,exit_code INTEGER,
          timed_out INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,
          summary TEXT NOT NULL DEFAULT '',evidence_handle TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT '',timeout_seconds REAL NOT NULL DEFAULT 0,
          stdout_path TEXT NOT NULL DEFAULT '',stderr_path TEXT NOT NULL DEFAULT '',
          repository_tree TEXT NOT NULL DEFAULT 'unknown',environment_hash TEXT NOT NULL DEFAULT 'unknown',
          project_id TEXT NOT NULL DEFAULT '');
        CREATE INDEX jobs_state_idx ON jobs(state,created_at DESC);
        CREATE TABLE completion_events(
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,
          exit_code INTEGER,completed_at REAL NOT NULL,evidence_handle TEXT NOT NULL,payload_json TEXT NOT NULL,
          FOREIGN KEY(job_id) REFERENCES jobs(job_id));
        CREATE TABLE verifier_results(
          cache_key TEXT PRIMARY KEY,command_json TEXT NOT NULL,tree_hash TEXT NOT NULL,
          environment_hash TEXT NOT NULL,dependency_hash TEXT NOT NULL,toolchain_hash TEXT NOT NULL,
          success INTEGER NOT NULL,exit_code INTEGER NOT NULL,evidence_handle TEXT NOT NULL,
          affected_paths_json TEXT NOT NULL,created_at REAL NOT NULL);
        INSERT INTO metadata VALUES('schema_version','2');
        """
    )
    return database


def _insert_job(
    database: sqlite3.Connection,
    state_root: Path,
    *,
    job_id: str,
    state: str,
    created_at: float,
    pid: int | None = None,
    completed_at: float | None = None,
    exit_code: int | None = None,
    cancelled: int = 0,
    evidence_handle: str = "",
) -> None:
    job_dir = state_root / "broker" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    database.execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            state,
            json.dumps(["python", "-c", "print('ok')"]),
            str(state_root),
            created_at,
            created_at if state == "RUNNING" else None,
            completed_at,
            pid,
            exit_code,
            0,
            cancelled,
            "summary" if completed_at is not None else "",
            evidence_handle,
            "",
            1200.0,
            str(job_dir / "stdout.log"),
            str(job_dir / "stderr.log"),
            "tree-a",
            "env-a",
            "project-a",
        ),
    )


def _clone(source: Path, python_state: Path, rust_state: Path) -> None:
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)


def _stable_job(value: dict[str, Any]) -> dict[str, Any]:
    output = dict(value)
    output["completed_at_is_number"] = isinstance(output.pop("completed_at"), (int, float))
    return output


def test_native_job_cancel_matches_python_marker_and_flag(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    database = _initialize(source)
    try:
        _insert_job(database, source, job_id="job-active", state="QUEUED", created_at=20.0)
        database.commit()
    finally:
        database.close()
    _clone(source, python_state, rust_state)

    python_code, python_result = _run(
        "python", project, python_state, "job", "cancel", "job-active"
    )
    rust_code, rust_result = _run(
        "rust", project, rust_state, "job", "cancel", "job-active"
    )

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["state"] == "QUEUED"
    assert rust_result["cancelled"] is True
    for state_root in (python_state, rust_state):
        assert (state_root / "broker" / "jobs" / "job-active" / "cancel").is_file()
        database = sqlite3.connect(state_root / "broker" / "broker.sqlite3")
        try:
            assert database.execute(
                "SELECT state,cancelled FROM jobs WHERE job_id='job-active'"
            ).fetchone() == ("QUEUED", 1)
        finally:
            database.close()


def test_native_job_cancel_final_state_is_noop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    database = _initialize(source)
    try:
        _insert_job(
            database,
            source,
            job_id="job-done",
            state="COMPLETED",
            created_at=10.0,
            completed_at=11.0,
            exit_code=0,
        )
        database.commit()
    finally:
        database.close()
    _clone(source, python_state, rust_state)

    python_result = _run("python", project, python_state, "job", "cancel", "job-done")[1]
    rust_result = _run("rust", project, rust_state, "job", "cancel", "job-done")[1]

    assert rust_result == python_result
    assert rust_result["cancelled"] is False
    assert not (python_state / "broker" / "jobs" / "job-done" / "cancel").exists()
    assert not (rust_state / "broker" / "jobs" / "job-done" / "cancel").exists()


def test_native_job_recover_matches_python_and_preserves_live_pid(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    database = _initialize(source)
    try:
        _insert_job(
            database,
            source,
            job_id="job-live",
            state="RUNNING",
            created_at=30.0,
            pid=os.getpid(),
            evidence_handle="sha256:" + "a" * 64,
        )
        _insert_job(
            database,
            source,
            job_id="job-dead",
            state="QUEUED",
            created_at=20.0,
            pid=None,
            evidence_handle="sha256:" + "b" * 64,
        )
        _insert_job(
            database,
            source,
            job_id="job-final",
            state="FAILED",
            created_at=10.0,
            completed_at=11.0,
            exit_code=2,
        )
        database.commit()
    finally:
        database.close()
    _clone(source, python_state, rust_state)

    python_code, python_result = _run("python", project, python_state, "job", "recover")
    rust_code, rust_result = _run("rust", project, rust_state, "job", "recover")

    assert rust_code == python_code == 0
    assert len(rust_result["orphaned"]) == len(python_result["orphaned"]) == 1
    assert _stable_job(rust_result["orphaned"][0]) == _stable_job(
        python_result["orphaned"][0]
    )
    assert rust_result["orphaned"][0]["job_id"] == "job-dead"
    assert rust_result["orphaned"][0]["state"] == "ORPHANED"
    assert rust_result["orphaned"][0]["error"] == "worker or process disappeared"

    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "broker" / "broker.sqlite3")
        try:
            states = dict(database.execute("SELECT job_id,state FROM jobs"))
            completion = database.execute(
                "SELECT job_id,state,exit_code,evidence_handle,payload_json FROM completion_events"
            ).fetchone()
        finally:
            database.close()
        assert states == {
            "job-live": "RUNNING",
            "job-dead": "ORPHANED",
            "job-final": "FAILED",
        }
        assert completion[:4] == (
            "job-dead",
            "ORPHANED",
            None,
            "sha256:" + "b" * 64,
        )
        assert json.loads(completion[4])["job_id"] == "job-dead"
        lines = [
            json.loads(line)
            for line in (state_root / "broker" / "completions.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(lines) == 1
        assert lines[0]["state"] == "ORPHANED"
        assert lines[0]["exit_code"] is None


def test_native_job_recover_empty_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"

    python_code, python_result = _run("python", project, python_state, "job", "recover")
    rust_code, rust_result = _run("rust", project, rust_state, "job", "recover")

    assert rust_code == python_code == 0
    assert rust_result == python_result == {"orphaned": []}
