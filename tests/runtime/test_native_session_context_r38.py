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
        timeout=300,
    )
    assert completed.returncode == 0, {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def _prepare_state(project: Path, state_root: Path, *, event_count: int) -> None:
    runtime = SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(session_id="sess-context", metadata={"task": "context parity"})
    for index in range(1, event_count + 1):
        event_type = "TASK" if index % 2 else "RESULT"
        runtime.append(
            "sess-context",
            event_type,
            {
                "task": f"iş-{index}",
                "result": "ok" if index % 2 == 0 else "pending",
                "index": index,
            },
        )


def _state_pair(
    tmp_path: Path,
    *,
    event_count: int,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_state(project, source, event_count=event_count)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return project, python_state, rust_state


def test_native_empty_session_context_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=0)
    arguments = ("session", "context", "sess-context")
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result == {
        "session_id": "sess-context",
        "budget": 32000,
        "used": 0,
        "sections": [],
        "root_summary_id": None,
        "recent_event_count": 0,
        "exact_history_events": 0,
    }


def test_native_recent_session_context_matches_python_text_and_tokens(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=3)
    arguments = (
        "session",
        "context",
        "sess-context",
        "--token-budget",
        "1000",
        "--recent-events=2",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert rust_result["root_summary_id"] is not None
    assert rust_result["recent_event_count"] == 2
    assert rust_result["exact_history_events"] == 3
    assert [section["id"] for section in rust_result["sections"]][-2:] == [
        "event:2",
        "event:3",
    ]
    assert '"iş-3"' in rust_result["sections"][-1]["text"]


def test_native_zero_recent_events_preserves_python_slice_semantics(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=4)
    arguments = (
        "session",
        "context",
        "sess-context",
        "--recent-events",
        "0",
        "--token-budget=10000",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert rust_result["root_summary_id"] is not None
    assert rust_result["recent_event_count"] == 4
    assert [row["id"] for row in rust_result["sections"] if row["role"] == "event"] == [
        "event:1",
        "event:2",
        "event:3",
        "event:4",
    ]


def test_native_negative_recent_events_matches_python_drop_prefix(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=5)
    arguments = (
        "session",
        "context",
        "sess-context",
        "--recent-events=-2",
        "--token-budget",
        "10000",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert rust_result["recent_event_count"] == 3
    assert [row["id"] for row in rust_result["sections"] if row["role"] == "event"] == [
        "event:3",
        "event:4",
        "event:5",
    ]


def test_native_negative_budget_selects_no_sections(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=2)
    arguments = (
        "session",
        "context",
        "sess-context",
        "--token-budget=-1",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert rust_result["used"] == 0
    assert rust_result["sections"] == []
    assert rust_result["recent_event_count"] == 2


def test_native_hierarchical_context_compaction_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=40)
    arguments = (
        "session",
        "context",
        "sess-context",
        "--recent-events",
        "5",
        "--token-budget",
        "20000",
    )
    rust_result = _run("rust", project, rust_state, *arguments)
    python_result = _run("python", project, python_state, *arguments)

    assert rust_result == python_result
    assert rust_result["root_summary_id"].startswith("sum-")
    assert rust_result["recent_event_count"] == 5
    assert rust_result["exact_history_events"] == 40
    assert rust_result["sections"][0]["role"] == "summary"
    assert rust_result["sections"][0]["range"] == [1, 40]

    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            levels = database.execute(
                "SELECT order_level,COUNT(*) FROM session_summaries GROUP BY order_level ORDER BY order_level"
            ).fetchall()
        finally:
            database.close()
        assert levels == [(0, 2), (1, 1)]
