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
) -> tuple[int, Any, str]:
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
    payload = json.loads(completed.stdout) if completed.stdout.strip() else None
    return completed.returncode, payload, completed.stderr


def _session_shape(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": value["session_id"],
        "project_id": value["project_id"],
        "parent_ids": value["parent_ids"],
        "state": value["state"],
        "metadata": value["metadata"],
        "created_at_is_number": isinstance(value["created_at"], (int, float)),
        "updated_at_is_number": isinstance(value["updated_at"], (int, float)),
    }


def _event_shape(value: dict[str, Any], *, keep_previous: bool = True) -> dict[str, Any]:
    output = {
        "session_id": value["session_id"],
        "sequence": value["sequence"],
        "event_type": value["event_type"],
        "payload": value["payload"],
        "event_hash_is_hex": len(value["event_hash"]) == 64
        and set(value["event_hash"]) <= set("0123456789abcdef"),
        "created_at_is_number": isinstance(value["created_at"], (int, float)),
    }
    if keep_previous:
        output["previous_hash"] = value["previous_hash"]
    return output


def _prepare_parent(project: Path, state_root: Path) -> None:
    runtime = SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(session_id="parent-a", metadata={"kind": "parent"})


def _prepare_events(project: Path, state_root: Path, *, count: int = 9) -> None:
    runtime = SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(session_id="sess-events", metadata={"task": "compact"})
    for index in range(1, count + 1):
        runtime.append(
            "sess-events",
            "TASK" if index % 2 else "RESULT",
            {"task": f"step-{index}", "result": "ok" if index % 2 == 0 else "pending"},
        )


def _clone_state(source: Path, python_state: Path, rust_state: Path) -> None:
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)


def test_native_session_open_matches_python_parent_and_task_semantics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_parent(project, source)
    _clone_state(source, python_state, rust_state)
    arguments = (
        "session",
        "open",
        "--session-id",
        "child-a",
        "--parent",
        "parent-a",
        "--parent=parent-a",
        "--task",
        "native parity",
    )

    python_code, python_result, python_error = _run(
        "python", project, python_state, *arguments
    )
    rust_code, rust_result, rust_error = _run("rust", project, rust_state, *arguments)

    assert (python_code, python_error) == (0, "")
    assert (rust_code, rust_error) == (0, "")
    assert _session_shape(rust_result) == _session_shape(python_result) == {
        "session_id": "child-a",
        "project_id": stable_project_id(project),
        "parent_ids": ["parent-a"],
        "state": "ACTIVE",
        "metadata": {"task": "native parity"},
        "created_at_is_number": True,
        "updated_at_is_number": True,
    }


def test_native_session_append_matches_python_event_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_events(project, source, count=0)
    _clone_state(source, python_state, rust_state)
    arguments = (
        "session",
        "append",
        "sess-events",
        "DECISION",
        json.dumps({"decision": "ship", "nested": {"b": 2, "a": 1}}),
    )

    python_code, python_result, _ = _run("python", project, python_state, *arguments)
    rust_code, rust_result, _ = _run("rust", project, rust_state, *arguments)

    assert rust_code == python_code == 0
    assert _event_shape(rust_result) == _event_shape(python_result) == {
        "session_id": "sess-events",
        "sequence": 1,
        "event_type": "DECISION",
        "payload": {"decision": "ship", "nested": {"a": 1, "b": 2}},
        "previous_hash": "0" * 64,
        "event_hash_is_hex": True,
        "created_at_is_number": True,
    }
    for state_root, result in ((python_state, python_result), (rust_state, rust_result)):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            row = database.execute(
                "SELECT sequence,event_type,payload_json,previous_hash,event_hash FROM session_events"
            ).fetchone()
        finally:
            database.close()
        assert row[:2] == (1, "DECISION")
        assert json.loads(row[2]) == result["payload"]
        assert row[3:] == ("0" * 64, result["event_hash"])


def test_native_session_compact_matches_python_custom_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_events(project, source, count=9)
    _clone_state(source, python_state, rust_state)
    arguments = (
        "session",
        "compact",
        "sess-events",
        "--leaf-size",
        "2",
        "--fanout=3",
    )

    python_code, python_result, _ = _run("python", project, python_state, *arguments)
    rust_code, rust_result, _ = _run("rust", project, rust_state, *arguments)

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["root_summary_id"].startswith("sum-")
    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            rows = database.execute(
                "SELECT summary_id,source_start,source_end,child_ids_json,source_hash,order_level,invalidated_at "
                "FROM session_summaries ORDER BY order_level,source_start,summary_id"
            ).fetchall()
        finally:
            database.close()
        normalized = [(*row[:3], json.loads(row[3]), *row[4:]) for row in rows]
        if state_root == python_state:
            expected = normalized
        else:
            assert normalized == expected
    assert [row[5] for row in expected].count(0) == 5
    assert [row[5] for row in expected].count(1) == 2
    assert [row[5] for row in expected].count(2) == 1


def test_native_session_compact_reuses_existing_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_events(project, source, count=4)
    _clone_state(source, python_state, rust_state)
    arguments = ("session", "compact", "sess-events")

    first_python = _run("python", project, python_state, *arguments)[1]
    first_rust = _run("rust", project, rust_state, *arguments)[1]
    second_python = _run("python", project, python_state, *arguments)[1]
    second_rust = _run("rust", project, rust_state, *arguments)[1]

    assert first_rust == first_python
    assert second_rust == second_python == first_python


def _write_export(project: Path, path: Path) -> int:
    state = path.parent / "export-source"
    runtime = SessionRuntime(
        state / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(
        session_id="source-session",
        metadata={"task": "import me", "imported_from": "metadata-wins"},
    )
    runtime.append("source-session", "TASK", {"task": "one"})
    runtime.append("source-session", "RESULT", {"result": "done"})
    runtime.export("source-session", path)
    return 2


def test_native_session_import_explicit_id_matches_python_without_quarantine(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    export_path = tmp_path / "session.json"
    event_count = _write_export(project, export_path)
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    arguments = (
        "session",
        "import",
        "--input",
        str(export_path),
        "--session-id=imported-session",
    )

    python_code, python_result, _ = _run("python", project, python_state, *arguments)
    rust_code, rust_result, _ = _run("rust", project, rust_state, *arguments)

    assert rust_code == python_code == 0, {
        "python": {"code": python_code, "result": python_result},
        "rust": {"code": rust_code, "result": rust_result},
    }
    assert _session_shape(rust_result) == _session_shape(python_result)
    assert rust_result["session_id"] == "imported-session"
    assert rust_result["parent_ids"] == []
    assert rust_result["metadata"] == {
        "imported_from": "metadata-wins",
        "task": "import me",
    }
    for state_root in (python_state, rust_state):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            events = database.execute(
                "SELECT sequence,event_type,payload_json FROM session_events ORDER BY sequence"
            ).fetchall()
            quarantine = database.execute(
                "SELECT COUNT(*) FROM session_quarantine"
            ).fetchone()[0]
        finally:
            database.close()
        assert len(events) == event_count
        assert [(row[0], row[1], json.loads(row[2])) for row in events] == [
            (1, "TASK", {"task": "one"}),
            (2, "RESULT", {"result": "done"}),
        ]
        assert quarantine == 0


def test_native_session_import_generated_id_quarantines_changed_hashes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    export_path = tmp_path / "session.json"
    event_count = _write_export(project, export_path)
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    arguments = ("session", "import", "--input", str(export_path))

    python_code, python_result, _ = _run("python", project, python_state, *arguments)
    rust_code, rust_result, _ = _run("rust", project, rust_state, *arguments)

    assert rust_code == python_code == 0
    for result in (python_result, rust_result):
        assert result["session_id"].startswith("sess-")
        assert len(result["session_id"]) == 37
        assert result["parent_ids"] == []
        assert result["metadata"] == {
            "imported_from": "metadata-wins",
            "task": "import me",
        }
    for state_root, result in ((python_state, python_result), (rust_state, rust_result)):
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            rows = database.execute(
                "SELECT session_id,object_type,object_id,reason,payload_json "
                "FROM session_quarantine ORDER BY quarantine_id"
            ).fetchall()
        finally:
            database.close()
        assert len(rows) == event_count
        assert [row[:4] for row in rows] == [
            (result["session_id"], "event", "1", "import-hash-changed"),
            (result["session_id"], "event", "2", "import-hash-changed"),
        ]
        assert [json.loads(row[4])["event_type"] for row in rows] == ["TASK", "RESULT"]


def test_native_session_import_rejects_bad_hash_without_mutation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    export_path = tmp_path / "session.json"
    _write_export(project, export_path)
    value = json.loads(export_path.read_text(encoding="utf-8"))
    value["export_hash"] = "f" * 64
    export_path.write_text(json.dumps(value), encoding="utf-8")

    for engine in ("python", "rust"):
        state_root = tmp_path / f"{engine}-state"
        code, _result, _error = _run(
            engine,
            project,
            state_root,
            "session",
            "import",
            "--input",
            str(export_path),
        )
        assert code != 0
        database = sqlite3.connect(state_root / "sessions.sqlite3")
        try:
            assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        finally:
            database.close()
