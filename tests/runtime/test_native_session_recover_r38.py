from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.session_runtime import SessionRuntime
from syntavra_runtime.util import stable_project_id

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


def _run(engine: str, project: Path, state_root: Path) -> tuple[int, Any]:
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
            "session",
            "recover",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _prepare_state(project: Path, state_root: Path) -> None:
    project_id = stable_project_id(project)
    runtime = SessionRuntime(state_root / "sessions.sqlite3", project_id=project_id)
    runtime.create_session(session_id="sess-a", metadata={"task": "first"})
    runtime.append("sess-a", "TASK", {"task": "implement"})
    runtime.create_session(session_id="sess-b", metadata={"task": "second"})
    runtime.append("sess-b", "RESULT", {"result": "passed"})

    database = sqlite3.connect(state_root / "sessions.sqlite3")
    try:
        database.execute(
            "UPDATE sessions SET updated_at=100.0 WHERE session_id='sess-a'"
        )
        database.execute(
            "UPDATE sessions SET updated_at=200.0 WHERE session_id='sess-b'"
        )
        database.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
            (
                "sess-foreign",
                "foreign-project",
                "[]",
                "ACTIVE",
                1.0,
                300.0,
                "{}",
            ),
        )
        database.commit()
    finally:
        database.close()


def _state_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_state(project, source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return project, python_state, rust_state


def test_native_empty_session_recovery_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"

    python_code, python_result = _run("python", project, python_state)
    rust_code, rust_result = _run("rust", project, rust_state)

    assert rust_code == python_code == 0
    assert rust_result == python_result == {
        "ok": True,
        "database_integrity": True,
        "sessions": {},
    }


def test_native_valid_session_recovery_matches_python_and_scope(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)

    python_code, python_result = _run("python", project, python_state)
    rust_code, rust_result = _run("rust", project, rust_state)

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["ok"] is True
    assert rust_result["database_integrity"] is True
    assert set(rust_result["sessions"]) == {"sess-a", "sess-b"}
    assert all(value["ok"] for value in rust_result["sessions"].values())


def test_native_recovery_fails_closed_on_one_tampered_session(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            database.execute(
                "UPDATE session_events SET event_hash=? WHERE session_id='sess-b' AND sequence=1",
                ("e" * 64,),
            )
            database.commit()
        finally:
            database.close()

    python_code, python_result = _run("python", project, python_state)
    rust_code, rust_result = _run("rust", project, rust_state)

    assert rust_code == python_code == 3
    assert rust_result == python_result
    assert rust_result["ok"] is False
    assert rust_result["database_integrity"] is True
    assert rust_result["sessions"]["sess-a"]["ok"] is True
    assert rust_result["sessions"]["sess-b"] == {
        "ok": False,
        "events": 1,
        "last_hash": "e" * 64,
        "reasons": ["event-hash-mismatch:1"],
    }
