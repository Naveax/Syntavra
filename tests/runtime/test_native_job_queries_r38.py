from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.state import StateDB

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


def _run(engine: str, state_root: Path, *arguments: str) -> Any:
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


def _job_values(
    job_id: str,
    state: str,
    created_at: float,
    *,
    argv: tuple[str, ...],
    exit_code: int | None,
    completed_at: float | None,
    summary: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "state": state,
        "argv_json": json.dumps(argv, ensure_ascii=False),
        "cwd": "/syntavra/project",
        "created_at": created_at,
        "started_at": created_at + 0.25,
        "completed_at": completed_at,
        "pid": None,
        "exit_code": exit_code,
        "timed_out": int(state == "TIMED_OUT"),
        "cancelled": int(state == "CANCELLED"),
        "summary": summary,
        "evidence_handle": "sc://sha256/" + job_id[-1] * 64,
        "error": "" if exit_code in {None, 0} else "command failed",
        "timeout_seconds": 1200.0,
        "stdout_path": f"/syntavra/jobs/{job_id}/stdout.log",
        "stderr_path": f"/syntavra/jobs/{job_id}/stderr.log",
        "repository_tree": f"tree-{job_id}",
        "environment_hash": f"environment-{job_id}",
        "project_id": "project-r38",
    }


def _prepare_state(state_root: Path) -> None:
    database = StateDB(state_root / "broker" / "broker.sqlite3")
    database.upsert_job(
        _job_values(
            "job-a",
            "RUNNING",
            100.0,
            argv=("python", "worker.py", "α"),
            exit_code=None,
            completed_at=None,
            summary="running",
        )
    )
    database.upsert_job(
        _job_values(
            "job-b",
            "COMPLETED",
            200.0,
            argv=("cargo", "test", "--locked"),
            exit_code=0,
            completed_at=210.0,
            summary="passed",
        )
    )
    database.upsert_job(
        _job_values(
            "job-c",
            "FAILED",
            300.0,
            argv=("python", "-m", "pytest", "-q"),
            exit_code=1,
            completed_at=320.0,
            summary="failed",
        )
    )
    database.record_completion(
        {
            "job_id": "job-b",
            "state": "COMPLETED",
            "exit_code": 0,
            "completed_at": 210.0,
            "evidence_handle": "sc://sha256/" + "b" * 64,
        }
    )
    database.record_completion(
        {
            "job_id": "job-c",
            "state": "FAILED",
            "exit_code": 1,
            "completed_at": 320.0,
            "evidence_handle": "sc://sha256/" + "c" * 64,
        }
    )


def _state_pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    _prepare_state(source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return python_state, rust_state


def test_native_empty_job_list_matches_python(tmp_path: Path) -> None:
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    assert _run("rust", rust_state, "job", "list") == _run(
        "python", python_state, "job", "list"
    ) == {"jobs": []}


def test_native_filtered_job_list_matches_python(tmp_path: Path) -> None:
    python_state, rust_state = _state_pair(tmp_path)
    arguments = (
        "job",
        "list",
        "--state",
        "COMPLETED",
        "--state=FAILED",
        "--limit",
        "2",
    )
    rust_result = _run("rust", rust_state, *arguments)
    python_result = _run("python", python_state, *arguments)

    assert rust_result == python_result
    assert [row["job_id"] for row in rust_result["jobs"]] == ["job-c", "job-b"]
    assert rust_result["jobs"][0]["argv"] == ["python", "-m", "pytest", "-q"]
    assert rust_result["jobs"][1]["timed_out"] is False


def test_native_job_show_matches_python_exact_record(tmp_path: Path) -> None:
    python_state, rust_state = _state_pair(tmp_path)
    rust_result = _run("rust", rust_state, "job", "show", "job-b")
    python_result = _run("python", python_state, "job", "show", "job-b")

    assert rust_result == python_result
    assert rust_result == {
        "job_id": "job-b",
        "state": "COMPLETED",
        "argv": ["cargo", "test", "--locked"],
        "cwd": "/syntavra/project",
        "created_at": 200.0,
        "started_at": 200.25,
        "completed_at": 210.0,
        "pid": None,
        "exit_code": 0,
        "timed_out": False,
        "cancelled": False,
        "summary": "passed",
        "evidence_handle": "sc://sha256/" + "b" * 64,
        "error": "",
        "project_id": "project-r38",
        "repository_tree": "tree-job-b",
        "environment_hash": "environment-job-b",
    }


def test_native_job_completions_pagination_matches_python(tmp_path: Path) -> None:
    python_state, rust_state = _state_pair(tmp_path)
    arguments = ("job", "completions", "--after", "1", "--limit=1")
    rust_result = _run("rust", rust_state, *arguments)
    python_result = _run("python", python_state, *arguments)

    assert rust_result == python_result
    assert rust_result == {
        "cursor": 2,
        "events": [
            {
                "sequence": 2,
                "job_id": "job-c",
                "state": "FAILED",
                "exit_code": 1,
                "completed_at": 320.0,
                "evidence_handle": "sc://sha256/" + "c" * 64,
            }
        ],
    }


def test_native_job_completion_cursor_bounds_match_python(tmp_path: Path) -> None:
    python_state, rust_state = _state_pair(tmp_path)
    first_arguments = ("job", "completions", "--after=-5", "--limit=0")
    assert _run("rust", rust_state, *first_arguments) == _run(
        "python", python_state, *first_arguments
    )

    exhausted_arguments = ("job", "completions", "--after", "99")
    assert _run("rust", rust_state, *exhausted_arguments) == _run(
        "python", python_state, *exhausted_arguments
    ) == {"cursor": 99, "events": []}
