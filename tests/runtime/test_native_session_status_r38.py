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


def _python_engine(project: Path, state: Path, *arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *arguments,
        ]
    )


def _rust_engine(project: Path, state: Path, *arguments: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *arguments,
        ]
    )


def test_native_empty_session_status_matches_python_exactly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()

    python_result = _python_engine(project, state, "run", "session-status")
    shutil.rmtree(state)
    rust_result = _rust_engine(project, state, "run", "session-status")

    assert rust_result == python_result


def test_native_populated_session_status_matches_python_exactly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()

    opened = _python_engine(
        project,
        state,
        "run",
        "session-open",
        "--session-id",
        "session-r38",
        "--metadata",
        json.dumps({"owner": "r38", "priority": 7}, separators=(",", ":")),
    )
    assert opened["ok"] is True

    python_result = _python_engine(project, state, "run", "session-status")
    rust_result = _rust_engine(project, state, "run", "session-status")

    assert rust_result == python_result
    assert rust_result["worker_alive"] is False
    assert rust_result["sessions"][0]["session_id"] == "session-r38"
    assert rust_result["analytics"]["events"] == 1
    assert rust_result["analytics"]["continuity"]["restores"] == 0
