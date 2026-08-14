from __future__ import annotations

import json
import re
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
SESSION_ID = re.compile(r"^sess-[0-9a-f]{32}$")
CHECKPOINT_ID = re.compile(r"^cp-[0-9a-f]{32}$")


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


def _runtime(project: Path, state_root: Path) -> SessionRuntime:
    return SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )


def _prepare_fork_state(project: Path, state_root: Path) -> None:
    runtime = _runtime(project, state_root)
    runtime.create_session(
        session_id="sess-parent",
        metadata={"task": "parent work", "owner": "Naveax"},
    )
    runtime.append("sess-parent", "TASK", {"task": "implement lifecycle"})
    runtime.append("sess-parent", "DECISION", {"decision": "fork native branch"})
    runtime.append("sess-parent", "RESULT", {"result": "ready"})


def _prepare_merge_state(project: Path, state_root: Path) -> None:
    runtime = _runtime(project, state_root)
    runtime.create_session(session_id="sess-a", metadata={"task": "left"})
    runtime.append("sess-a", "TASK", {"task": "left work"})
    runtime.append("sess-a", "RESULT", {"result": "left-ready"})
    runtime.create_session(session_id="sess-b", metadata={"task": "right"})
    runtime.append("sess-b", "TASK", {"task": "right work"})


def _state_pair(
    tmp_path: Path,
    prepare: Any,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    prepare(project, source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return project, python_state, rust_state


def _checkpoint_rows(state_root: Path, session_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    database = sqlite3.connect(state_root / "sessions.sqlite3")
    database.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in session_ids)
        rows = database.execute(
            f"SELECT checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at "
            f"FROM session_checkpoints WHERE session_id IN ({placeholders}) ORDER BY session_id,created_at",
            session_ids,
        ).fetchall()
    finally:
        database.close()
    return [
        {
            **dict(row),
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in rows
    ]


def _event_rows(state_root: Path, session_id: str) -> list[dict[str, Any]]:
    database = sqlite3.connect(state_root / "sessions.sqlite3")
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at "
            "FROM session_events WHERE session_id=? ORDER BY sequence",
            (session_id,),
        ).fetchall()
    finally:
        database.close()
    return [
        {
            **dict(row),
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def _normalized_created_session(
    result: dict[str, Any],
    *,
    metadata_key: str,
) -> tuple[dict[str, Any], str, list[str]]:
    session_id = result["session_id"]
    assert SESSION_ID.fullmatch(session_id)
    metadata = dict(result["metadata"])
    raw_ids = metadata[metadata_key]
    checkpoint_ids = [raw_ids] if isinstance(raw_ids, str) else list(raw_ids)
    assert checkpoint_ids
    assert all(CHECKPOINT_ID.fullmatch(value) for value in checkpoint_ids)
    metadata[metadata_key] = ["<checkpoint>"] * len(checkpoint_ids)
    normalized = {
        **result,
        "session_id": "<session>",
        "created_at": "<time>",
        "updated_at": "<time>",
        "metadata": metadata,
    }
    return normalized, session_id, checkpoint_ids


def _normalized_checkpoints(
    rows: list[dict[str, Any]],
    checkpoint_ids: list[str],
) -> list[dict[str, Any]]:
    mapping = {value: f"<checkpoint-{index}>" for index, value in enumerate(checkpoint_ids)}
    normalized = []
    for row in rows:
        assert CHECKPOINT_ID.fullmatch(row["checkpoint_id"])
        value = dict(row)
        value["checkpoint_id"] = mapping[row["checkpoint_id"]]
        value["created_at"] = "<time>"
        value.pop("metadata_json")
        normalized.append(value)
    return normalized


def test_native_session_fork_matches_python_relationships(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, _prepare_fork_state)
    arguments = ("session", "fork", "sess-parent", "--label", "native-child")

    python_result = _run("python", project, python_state, *arguments)
    rust_result = _run("rust", project, rust_state, *arguments)

    python_normalized, python_child, python_checkpoints = _normalized_created_session(
        python_result,
        metadata_key="fork_checkpoint",
    )
    rust_normalized, rust_child, rust_checkpoints = _normalized_created_session(
        rust_result,
        metadata_key="fork_checkpoint",
    )
    assert rust_normalized == python_normalized == {
        "session_id": "<session>",
        "project_id": stable_project_id(project),
        "parent_ids": ["sess-parent"],
        "state": "ACTIVE",
        "created_at": "<time>",
        "updated_at": "<time>",
        "metadata": {
            "fork_checkpoint": ["<checkpoint>"],
            "label": "native-child",
        },
    }

    python_checkpoint_rows = _checkpoint_rows(python_state, ("sess-parent",))
    rust_checkpoint_rows = _checkpoint_rows(rust_state, ("sess-parent",))
    assert _normalized_checkpoints(rust_checkpoint_rows, rust_checkpoints) == _normalized_checkpoints(
        python_checkpoint_rows,
        python_checkpoints,
    )
    assert rust_checkpoint_rows[0]["metadata"] == {"reason": "fork-source"}
    assert rust_checkpoint_rows[0]["through_sequence"] == 3

    python_event = _event_rows(python_state, python_child)
    rust_event = _event_rows(rust_state, rust_child)
    assert len(python_event) == len(rust_event) == 1
    for row, child_id, checkpoint_id in (
        (python_event[0], python_child, python_checkpoints[0]),
        (rust_event[0], rust_child, rust_checkpoints[0]),
    ):
        assert row["session_id"] == child_id
        assert row["sequence"] == 1
        assert row["event_type"] == "session-fork"
        assert row["previous_hash"] == "0" * 64
        assert len(row["event_hash"]) == 64
        assert row["payload"] == {
            "parent_session": "sess-parent",
            "checkpoint": checkpoint_id,
            "through_sequence": 3,
        }


def test_native_session_merge_matches_python_dedupe_and_cross_references(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, _prepare_merge_state)
    arguments = (
        "session",
        "merge",
        "sess-a",
        "sess-b",
        "sess-a",
        "--label=combined",
    )

    python_result = _run("python", project, python_state, *arguments)
    rust_result = _run("rust", project, rust_state, *arguments)

    python_normalized, python_merged, python_checkpoints = _normalized_created_session(
        python_result,
        metadata_key="merge_checkpoints",
    )
    rust_normalized, rust_merged, rust_checkpoints = _normalized_created_session(
        rust_result,
        metadata_key="merge_checkpoints",
    )
    assert rust_normalized == python_normalized == {
        "session_id": "<session>",
        "project_id": stable_project_id(project),
        "parent_ids": ["sess-a", "sess-b"],
        "state": "ACTIVE",
        "created_at": "<time>",
        "updated_at": "<time>",
        "metadata": {
            "label": "combined",
            "merge_checkpoints": ["<checkpoint>", "<checkpoint>"],
        },
    }

    python_checkpoint_rows = _checkpoint_rows(python_state, ("sess-a", "sess-b"))
    rust_checkpoint_rows = _checkpoint_rows(rust_state, ("sess-a", "sess-b"))
    assert _normalized_checkpoints(rust_checkpoint_rows, rust_checkpoints) == _normalized_checkpoints(
        python_checkpoint_rows,
        python_checkpoints,
    )
    assert [row["session_id"] for row in rust_checkpoint_rows] == ["sess-a", "sess-b"]
    assert all(row["metadata"] == {"reason": "merge-source"} for row in rust_checkpoint_rows)
    assert [row["through_sequence"] for row in rust_checkpoint_rows] == [2, 1]

    python_event = _event_rows(python_state, python_merged)
    rust_event = _event_rows(rust_state, rust_merged)
    assert len(python_event) == len(rust_event) == 1
    for row, merged_id, checkpoint_ids in (
        (python_event[0], python_merged, python_checkpoints),
        (rust_event[0], rust_merged, rust_checkpoints),
    ):
        assert row["session_id"] == merged_id
        assert row["event_type"] == "session-merge"
        assert row["previous_hash"] == "0" * 64
        assert row["payload"] == {
            "parents": ["sess-a", "sess-b"],
            "checkpoints": checkpoint_ids,
        }


def test_native_session_close_matches_python_checkpoint_and_state(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, _prepare_fork_state)
    arguments = ("session", "close", "sess-parent")

    python_result = _run("python", project, python_state, *arguments)
    rust_result = _run("rust", project, rust_state, *arguments)

    def normalize(value: dict[str, Any]) -> dict[str, Any]:
        return {
            **value,
            "created_at": "<time>",
            "updated_at": "<time>",
        }

    assert normalize(rust_result) == normalize(python_result) == {
        "session_id": "sess-parent",
        "project_id": stable_project_id(project),
        "parent_ids": [],
        "state": "CLOSED",
        "created_at": "<time>",
        "updated_at": "<time>",
        "metadata": {"owner": "Naveax", "task": "parent work"},
    }

    python_rows = _checkpoint_rows(python_state, ("sess-parent",))
    rust_rows = _checkpoint_rows(rust_state, ("sess-parent",))
    assert len(python_rows) == len(rust_rows) == 1
    assert python_rows[0]["metadata"] == rust_rows[0]["metadata"] == {"reason": "close"}
    assert python_rows[0]["through_sequence"] == rust_rows[0]["through_sequence"] == 3
    assert python_rows[0]["event_hash"] == rust_rows[0]["event_hash"]
    assert python_rows[0]["root_summary_id"] == rust_rows[0]["root_summary_id"]


def test_native_empty_session_close_matches_python_zero_hash(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    runtime = _runtime(project, source)
    runtime.create_session(session_id="sess-empty", metadata={})
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)

    python_result = _run("python", project, python_state, "session", "close", "sess-empty")
    rust_result = _run("rust", project, rust_state, "session", "close", "sess-empty")

    assert python_result["state"] == rust_result["state"] == "CLOSED"
    python_rows = _checkpoint_rows(python_state, ("sess-empty",))
    rust_rows = _checkpoint_rows(rust_state, ("sess-empty",))
    assert python_rows[0]["through_sequence"] == rust_rows[0]["through_sequence"] == 0
    assert python_rows[0]["root_summary_id"] is rust_rows[0]["root_summary_id"] is None
    assert python_rows[0]["event_hash"] == rust_rows[0]["event_hash"] == "0" * 64
