from __future__ import annotations

import json
import shutil
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


def _line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"


def test_native_rollout_tail_matches_incremental_python_polling(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_cursor = tmp_path / "python-cursor.json"
    rust_cursor = tmp_path / "rust-cursor.json"

    model = {
        "id": "event-1",
        "type": "response.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "output_tokens": 10,
            "reasoning_tokens": 5,
        },
    }
    tool = {
        "id": "event-2",
        "type": "tool_call",
        "name": "exec_command",
        "arguments": {"action": "wait"},
    }
    partial = b'{"id":"event-3","type":"response.completed","input_tokens":40'
    rollout.write_bytes(
        _line(model)
        + _line(tool)
        + _line(tool)
        + b"not-json\n"
        + partial
    )

    python_code, python_first = _run(
        "python",
        project,
        python_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(python_cursor),
    )
    rust_code, rust_first = _run(
        "rust",
        project,
        rust_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(rust_cursor),
    )
    assert rust_code == python_code == 0
    assert rust_first == python_first
    assert rust_first["processed_events"] == 2
    assert rust_first["partial_bytes"] == len(partial)
    assert rust_first["counters"] == {
        "model_turns": 1,
        "tool_calls": 1,
        "wait_calls": 1,
        "command_calls": 1,
        "compactions": 0,
        "fresh_input_tokens": 80,
        "cached_input_tokens": 20,
        "output_tokens": 10,
        "reasoning_tokens": 5,
        "malformed_lines": 1,
        "duplicate_events": 1,
    }
    assert json.loads(rust_cursor.read_text(encoding="utf-8")) == json.loads(
        python_cursor.read_text(encoding="utf-8")
    )

    with rollout.open("ab") as handle:
        handle.write(b',"cached_input_tokens":10}\n')
        handle.write(
            _line(
                {
                    "id": "event-4",
                    "type": "assistant_message",
                    "message": "compact_context",
                }
            )
        )

    python_second = _run(
        "python",
        project,
        python_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(python_cursor),
    )
    rust_second = _run(
        "rust",
        project,
        rust_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(rust_cursor),
    )
    assert rust_second == python_second
    assert rust_second[1]["processed_events"] == 2
    assert rust_second[1]["partial_bytes"] == 0
    assert rust_second[1]["counters"]["model_turns"] == 3
    assert rust_second[1]["counters"]["compactions"] == 1
    assert rust_second[1]["counters"]["fresh_input_tokens"] == 110
    assert rust_second[1]["counters"]["cached_input_tokens"] == 30

    python_empty = _run(
        "python",
        project,
        python_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(python_cursor),
    )
    rust_empty = _run(
        "rust",
        project,
        rust_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(rust_cursor),
    )
    assert rust_empty == python_empty
    assert rust_empty[1]["processed_events"] == 0


def test_native_rollout_tail_matches_python_truncation_reset(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_cursor = tmp_path / "python-cursor.json"
    rust_cursor = tmp_path / "rust-cursor.json"
    rollout.write_bytes(
        _line(
            {
                "id": "old-event",
                "type": "response.completed",
                "input_tokens": 500,
            }
        )
        + b"x" * 256
    )

    for engine, state_root, cursor in (
        ("python", python_state, python_cursor),
        ("rust", rust_state, rust_cursor),
    ):
        _run(
            engine,
            project,
            state_root,
            "rollout-tail",
            "--rollout",
            str(rollout),
            "--state-file",
            str(cursor),
        )

    rollout.write_bytes(
        _line(
            {
                "id": "new-event",
                "type": "response.completed",
                "input_tokens": 20,
                "cached_input_tokens": 5,
            }
        )
    )
    python_result = _run(
        "python",
        project,
        python_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(python_cursor),
    )
    rust_result = _run(
        "rust",
        project,
        rust_state,
        "rollout-tail",
        "--rollout",
        str(rollout),
        "--state-file",
        str(rust_cursor),
    )
    assert rust_result == python_result
    assert rust_result[1]["processed_events"] == 1
    assert rust_result[1]["counters"]["model_turns"] == 1
    assert rust_result[1]["counters"]["fresh_input_tokens"] == 15
    assert rust_result[1]["counters"]["cached_input_tokens"] == 5
    assert rust_result[1]["counters"]["malformed_lines"] == 0


def test_native_rollout_tail_no_candidate_matches_python_exit_two(tmp_path: Path) -> None:
    project = tmp_path / "project"
    codex_home = tmp_path / "empty-codex"
    project.mkdir()
    codex_home.mkdir()

    python_result = _run(
        "python",
        project,
        tmp_path / "python-state",
        "--codex-home",
        str(codex_home),
        "rollout-tail",
    )
    rust_result = _run(
        "rust",
        project,
        tmp_path / "rust-state",
        "--codex-home",
        str(codex_home),
        "rollout-tail",
    )
    assert rust_result == python_result == (2, {"ok": False, "reason": "no rollout found"})
