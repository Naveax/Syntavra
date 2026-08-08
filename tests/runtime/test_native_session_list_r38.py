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


def _run(engine: str, project: Path, state_root: Path, *arguments: str) -> Any:
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
    assert completed.returncode == 0, {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def _prepare_state(project: Path, state_root: Path) -> None:
    project_id = stable_project_id(project)
    runtime = SessionRuntime(state_root / "sessions.sqlite3", project_id=project_id)
    runtime.create_session(
        session_id="sess-a",
        metadata={"task": "index repository", "priority": 1},
    )
    runtime.create_session(
        session_id="sess-b",
        parent_ids=("sess-a",),
        metadata={"task": "verify native parity", "priority": 2},
    )
    runtime.create_session(
        session_id="sess-c",
        metadata={"task": "closed audit", "priority": 3},
    )

    database = sqlite3.connect(state_root / "sessions.sqlite3")
    try:
        database.execute(
            "UPDATE sessions SET created_at=10.0,updated_at=100.0 WHERE session_id='sess-a'"
        )
        database.execute(
            "UPDATE sessions SET created_at=20.0,updated_at=300.0 WHERE session_id='sess-b'"
        )
        database.execute(
            "UPDATE sessions SET state='CLOSED',created_at=30.0,updated_at=200.0 WHERE session_id='sess-c'"
        )
        database.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
            (
                "sess-foreign",
                "foreign-project",
                "[]",
                "ACTIVE",
                40.0,
                400.0,
                json.dumps({"task": "must not leak"}, sort_keys=True),
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


def test_native_empty_session_list_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    assert _run("rust", project, rust_state, "session", "list") == _run(
        "python", project, python_state, "session", "list"
    ) == {"sessions": []}


def test_native_session_list_matches_python_order_and_scope(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    rust_result = _run("rust", project, rust_state, "session", "list")
    python_result = _run("python", project, python_state, "session", "list")

    assert rust_result == python_result
    assert [row["session_id"] for row in rust_result["sessions"]] == [
        "sess-b",
        "sess-c",
        "sess-a",
    ]
    assert all(row["project_id"] == stable_project_id(project) for row in rust_result["sessions"])
    assert rust_result["sessions"][0]["parent_ids"] == ["sess-a"]
    assert rust_result["sessions"][0]["metadata"] == {
        "priority": 2,
        "task": "verify native parity",
    }


def test_native_session_state_filter_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    arguments = ("session", "list", "--state", "ACTIVE")
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert [row["session_id"] for row in rust_result["sessions"]] == [
        "sess-b",
        "sess-a",
    ]


def test_native_repeated_session_state_uses_last_value(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path)
    arguments = (
        "session",
        "list",
        "--state",
        "ACTIVE",
        "--state=CLOSED",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert [row["session_id"] for row in rust_result["sessions"]] == ["sess-c"]
