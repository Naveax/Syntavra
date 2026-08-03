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


def _run(
    engine: str,
    project: Path,
    state_root: Path,
    *arguments: str,
) -> tuple[int, Any]:
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


def _prepare_state(project: Path, state_root: Path, *, with_events: bool = True) -> None:
    runtime = SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(session_id="sess-verify", metadata={"task": "verify chain"})
    if with_events:
        runtime.append("sess-verify", "TASK", {"task": "implement native parity"})
        runtime.append(
            "sess-verify",
            "RESULT",
            {"result": "passed", "paths": ["src/lib.rs", "tests/runtime"]},
        )


def _state_pair(
    tmp_path: Path,
    *,
    with_events: bool = True,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_state(project, source, with_events=with_events)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return project, python_state, rust_state


def _mutate_both(
    python_state: Path,
    rust_state: Path,
    statement: str,
    parameters: tuple[Any, ...] = (),
) -> None:
    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            database.execute(statement, parameters)
            database.commit()
        finally:
            database.close()


def test_native_valid_session_verification_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    python_code, python_result = _run(
        "python", project, python_state, "session", "verify", "sess-verify"
    )
    rust_code, rust_result = _run(
        "rust", project, rust_state, "session", "verify", "sess-verify"
    )

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["ok"] is True
    assert rust_result["events"] == 2
    assert rust_result["reasons"] == []
    assert len(rust_result["last_hash"]) == 64


def test_native_empty_session_verification_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, with_events=False)
    python_code, python_result = _run(
        "python", project, python_state, "session", "verify", "sess-verify"
    )
    rust_code, rust_result = _run(
        "rust", project, rust_state, "session", "verify", "sess-verify"
    )

    assert rust_code == python_code == 0
    assert rust_result == python_result == {
        "ok": True,
        "events": 0,
        "last_hash": "0" * 64,
        "reasons": [],
    }


def test_native_tampered_event_hash_matches_python_failure(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    _mutate_both(
        python_state,
        rust_state,
        "UPDATE session_events SET event_hash=? WHERE session_id=? AND sequence=2",
        ("d" * 64, "sess-verify"),
    )

    python_code, python_result = _run(
        "python", project, python_state, "session", "verify", "sess-verify"
    )
    rust_code, rust_result = _run(
        "rust", project, rust_state, "session", "verify", "sess-verify"
    )

    assert rust_code == python_code == 3
    assert rust_result == python_result
    assert rust_result == {
        "ok": False,
        "events": 2,
        "last_hash": "d" * 64,
        "reasons": ["event-hash-mismatch:2"],
    }


def test_native_sequence_gap_matches_python_failure_order(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    _mutate_both(
        python_state,
        rust_state,
        "UPDATE session_events SET sequence=3 WHERE session_id=? AND sequence=2",
        ("sess-verify",),
    )

    python_code, python_result = _run(
        "python", project, python_state, "session", "verify", "sess-verify"
    )
    rust_code, rust_result = _run(
        "rust", project, rust_state, "session", "verify", "sess-verify"
    )

    assert rust_code == python_code == 3
    assert rust_result == python_result
    assert rust_result["ok"] is False
    assert rust_result["events"] == 3
    assert rust_result["reasons"] == [
        "sequence-gap:2->3",
        "event-hash-mismatch:3",
    ]
