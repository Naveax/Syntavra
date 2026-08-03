from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.session_runtime import SessionRuntime
from syntavra_runtime.util import canonical_json, stable_project_id

ROOT = Path(__file__).resolve().parents[2]
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


def _prepare_state(project: Path, state_root: Path, *, event_count: int) -> None:
    runtime = SessionRuntime(
        state_root / "sessions.sqlite3",
        project_id=stable_project_id(project),
    )
    runtime.create_session(
        session_id="sess-archive",
        metadata={"task": "archive parity", "owner": "Naveax"},
    )
    for index in range(1, event_count + 1):
        runtime.append(
            "sess-archive",
            "TASK" if index % 2 else "RESULT",
            {
                "task": f"native-check-{index}",
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


def _stable_checkpoint(value: dict[str, Any]) -> dict[str, Any]:
    assert CHECKPOINT_ID.fullmatch(value["checkpoint_id"])
    assert value["created_at"] > 0
    return {
        key: item
        for key, item in value.items()
        if key not in {"checkpoint_id", "created_at"}
    }


def _checkpoint_database_state(state_root: Path) -> dict[str, Any]:
    database = sqlite3.connect(state_root / "sessions.sqlite3")
    database.row_factory = sqlite3.Row
    try:
        checkpoint = database.execute(
            "SELECT session_id,through_sequence,root_summary_id,event_hash,metadata_json "
            "FROM session_checkpoints ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        summaries = database.execute(
            "SELECT summary_id,session_id,content,source_start,source_end,source_hash,order_level,invalidated_at "
            "FROM session_summaries ORDER BY order_level,source_start,source_end"
        ).fetchall()
    finally:
        database.close()
    assert checkpoint is not None
    checkpoint_value = dict(checkpoint)
    checkpoint_value["metadata_json"] = json.loads(checkpoint_value["metadata_json"])
    return {
        "checkpoint": checkpoint_value,
        "summaries": [dict(row) for row in summaries],
    }


def test_native_empty_checkpoint_matches_python_semantics(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=0)
    arguments = ("session", "checkpoint", "sess-archive", "--label", "empty")

    python_result = _run("python", project, python_state, *arguments)
    rust_result = _run("rust", project, rust_state, *arguments)

    assert _stable_checkpoint(rust_result) == _stable_checkpoint(python_result) == {
        "session_id": "sess-archive",
        "through_sequence": 0,
        "root_summary_id": None,
        "event_hash": "0" * 64,
        "metadata": {"label": "empty"},
    }
    assert _checkpoint_database_state(rust_state) == _checkpoint_database_state(python_state)


def test_native_checkpoint_matches_python_hash_and_compaction_state(tmp_path: Path) -> None:
    project, python_state, rust_state = _state_pair(tmp_path, event_count=40)
    arguments = (
        "session",
        "checkpoint",
        "sess-archive",
        "--label=release-candidate",
    )

    python_result = _run("python", project, python_state, *arguments)
    rust_result = _run("rust", project, rust_state, *arguments)

    assert _stable_checkpoint(rust_result) == _stable_checkpoint(python_result)
    assert rust_result["through_sequence"] == 40
    assert rust_result["metadata"] == {"label": "release-candidate"}
    assert rust_result["root_summary_id"].startswith("sum-")
    assert len(rust_result["event_hash"]) == 64

    rust_state_view = _checkpoint_database_state(rust_state)
    python_state_view = _checkpoint_database_state(python_state)
    assert rust_state_view == python_state_view
    assert [row["order_level"] for row in rust_state_view["summaries"]] == [0, 0, 1]
    assert rust_state_view["summaries"][-1]["source_start"] == 1
    assert rust_state_view["summaries"][-1]["source_end"] == 40


def _prepare_fixed_export_state(project: Path, state_root: Path) -> None:
    _prepare_state(project, state_root, event_count=3)
    database = sqlite3.connect(state_root / "sessions.sqlite3")
    try:
        last_hash = database.execute(
            "SELECT event_hash FROM session_events WHERE session_id='sess-archive' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
        database.execute(
            "INSERT INTO session_checkpoints("
            "checkpoint_id,session_id,through_sequence,root_summary_id,event_hash,metadata_json,created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                "cp-11111111111111111111111111111111",
                "sess-archive",
                3,
                None,
                last_hash,
                json.dumps({"label": "frozen"}, ensure_ascii=False, sort_keys=True),
                1234.5,
            ),
        )
        database.commit()
    finally:
        database.close()


def test_native_export_matches_python_bytes_hash_and_permissions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _prepare_fixed_export_state(project, source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)

    output = tmp_path / "nested" / "session-export.json"
    arguments = (
        "session",
        "export",
        "sess-archive",
        "--output",
        str(output),
    )

    python_result = _run("python", project, python_state, *arguments)
    python_bytes = output.read_bytes()
    output.write_bytes(b"stale-data-that-must-be-replaced")
    rust_result = _run("rust", project, rust_state, *arguments)
    rust_bytes = output.read_bytes()

    assert rust_result == python_result
    assert rust_bytes == python_bytes
    assert rust_bytes.endswith(b"\n")
    assert b"stale-data" not in rust_bytes

    payload = json.loads(rust_bytes)
    export_hash = payload.pop("export_hash")
    assert export_hash == rust_result["hash"]
    assert export_hash == hashlib.sha256(canonical_json(payload)).hexdigest()
    assert rust_result["events"] == 3
    assert payload["schema_version"] == 1
    assert payload["project_id"] == stable_project_id(project)
    assert payload["session"]["session_id"] == "sess-archive"
    assert len(payload["events"]) == 3
    assert payload["verification"]["ok"] is True
    assert payload["checkpoints"] == [
        {
            "checkpoint_id": "cp-11111111111111111111111111111111",
            "session_id": "sess-archive",
            "through_sequence": 3,
            "root_summary_id": None,
            "event_hash": payload["verification"]["last_hash"],
            "metadata_json": json.dumps(
                {"label": "frozen"}, ensure_ascii=False, sort_keys=True
            ),
            "created_at": 1234.5,
            "metadata": {"label": "frozen"},
        }
    ]

    if os.name != "nt":
        mode = stat.S_IMODE(output.stat().st_mode)
        assert mode == 0o600
