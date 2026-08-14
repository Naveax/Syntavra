from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _assert_equal, _normalized, _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, project: Path, *arguments: str) -> tuple[int, Any, str]:
    state = project / "state"
    home = project / "home"
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = ""
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state),
            "status",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _assert_pair(tmp_path: Path, *arguments: str) -> tuple[Any, Any]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    python_result = _run("python", python_project, *arguments)
    rust_result = _run("rust", rust_project, *arguments)
    python_code, python_value, python_stderr = python_result
    rust_code, rust_value, rust_stderr = rust_result
    assert rust_code == python_code, {"python": python_result, "rust": rust_result}
    assert rust_stderr == python_stderr == ""
    _assert_equal(
        _normalized(rust_value, rust_project),
        _normalized(python_value, python_project),
        f"status-{'-'.join(arguments) or 'default'}",
    )
    return python_value, rust_value


def test_native_status_default_empty_project_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path)
    assert set(value) == {
        "product",
        "version",
        "channel",
        "role",
        "doctor",
        "stats",
        "savings",
        "profile",
        "readiness",
        "evidence",
        "proxy_presets",
        "platform",
        "competitive_features",
        "primary_workflow",
    }


def test_native_status_doctor_focus_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path, "--doctor")
    assert value["doctor"]["ok"] is True


def test_native_status_savings_focus_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path, "--savings")
    assert value["savings"]["receipts"] == 0


def test_native_status_profile_focus_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path, "--profile")
    assert value["profile"]["name"] == "minimal"
    assert value["profile"]["max_active_tools"] == 8


def test_native_status_invalid_profile_file_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    for project in (python_project, rust_project):
        project.mkdir()
        state = project / "state"
        state.mkdir()
        (state / "mcp-profile.json").write_text("{invalid", encoding="utf-8")
    python_result = _run("python", python_project, "--profile")
    rust_result = _run("rust", rust_project, "--profile")
    assert rust_result[0] == python_result[0]
    assert rust_result[2] == python_result[2] == ""
    _assert_equal(rust_result[1], python_result[1], "status-invalid-profile")
    assert rust_result[1]["profile"] == {
        "name": "minimal",
        "invalid_profile_file": True,
    }


def test_native_status_memory_focus_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path, "--memory")
    assert value["memory"]["worker_alive"] is False
    assert value["memory"]["sessions"] == []
    assert value["memory"]["last_cycle"]["state"] == "IDLE"


def test_native_status_evidence_focus_matches_python(tmp_path: Path) -> None:
    _, value = _assert_pair(tmp_path, "--evidence")
    assert value["evidence"]["provider_usage"]["ok"] is True
    assert value["evidence"]["token_attribution"]["receipts"] == 0


def test_native_status_all_focus_flags_match_python(tmp_path: Path) -> None:
    _, value = _assert_pair(
        tmp_path,
        "--evidence",
        "--memory",
        "--profile",
        "--savings",
        "--doctor",
        "--doctor",
    )
    assert set(value) == {
        "product",
        "version",
        "channel",
        "doctor",
        "savings",
        "profile",
        "memory",
        "evidence",
    }
