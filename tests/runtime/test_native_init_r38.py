from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.util import stable_project_id

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "syntavra"


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    selected = os.environ.get("SYNTAVRA_R38_SELECTOR")
    if selected:
        path = Path(selected)
        assert path.is_file(), path
        return path
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bin", "syntavra"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    path = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert path.is_file(), path
    return path


def _run(engine: str, project: Path, *, host: str = "codex") -> tuple[int, Any, str]:
    state = project / "state"
    codex_home = project / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
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
            str(state),
            "--skill-root",
            str(SKILL_ROOT),
            "--codex-home",
            str(codex_home),
            "--host",
            host,
            "init",
            "index repository",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _normalize(value: Any, project: Path) -> Any:
    result = json.loads(json.dumps(value))
    session = result["session"]
    assert session["project_id"] == stable_project_id(project)
    assert session["session_id"].startswith("sc-")
    assert isinstance(session["started_at"], float)
    session["project"] = "<PROJECT>"
    session["project_id"] = "<PROJECT_ID>"
    session["session_id"] = "<SESSION_ID>"
    session["started_at"] = "<STARTED_AT>"
    return result


def _verify_side_effects(project: Path, value: Any) -> None:
    state = project / "state"
    session_id = value["session"]["session_id"]
    receipt = state / "sessions" / session_id / "session.json"
    assert receipt.is_file(), receipt
    assert json.loads(receipt.read_text(encoding="utf-8")) == value["session"]

    runtime = sqlite3.connect(state / "runtime.sqlite3")
    try:
        assert runtime.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in runtime.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        runtime.close()
    assert {"metadata", "jobs", "completion_events", "verifier_results"} <= tables

    evidence = sqlite3.connect(state / "evidence" / "evidence.sqlite3")
    try:
        assert evidence.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        evidence.close()


def test_native_init_active_runtime_matches_python(tmp_path: Path) -> None:
    projects = {
        "python": tmp_path / "python-project",
        "rust": tmp_path / "rust-project",
    }
    results: dict[str, tuple[int, Any, str]] = {}
    for engine, project in projects.items():
        project.mkdir()
        results[engine] = _run(engine, project)
        _verify_side_effects(project, results[engine][1])

    python_code, python_value, python_stderr = results["python"]
    rust_code, rust_value, rust_stderr = results["rust"]
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    assert _normalize(rust_value, projects["rust"]) == _normalize(
        python_value, projects["python"]
    )
    assert rust_value["health"]["state"] == "RUNTIME_ACTIVE"
    assert rust_value["health"]["healthy"] is True
    assert rust_value["health"]["reasons"] == []
    assert rust_value["session"]["activation_state"] == "RUNTIME_ACTIVE"


def test_native_init_unknown_host_degrades_exactly_like_python(tmp_path: Path) -> None:
    projects = {
        "python": tmp_path / "python-project",
        "rust": tmp_path / "rust-project",
    }
    results: dict[str, tuple[int, Any, str]] = {}
    for engine, project in projects.items():
        project.mkdir()
        results[engine] = _run(engine, project, host="unknown-host")
        _verify_side_effects(project, results[engine][1])

    python_code, python_value, python_stderr = results["python"]
    rust_code, rust_value, rust_stderr = results["rust"]
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    assert _normalize(rust_value, projects["rust"]) == _normalize(
        python_value, projects["python"]
    )
    assert rust_value["health"]["state"] == "RUNTIME_DEGRADED"
    assert rust_value["health"]["healthy"] is False
    assert rust_value["health"]["reasons"] == ["check-failed:host_adapter"]
    assert rust_value["health"]["details"]["host_negotiation"]["mode"] == "UNSUPPORTED"
