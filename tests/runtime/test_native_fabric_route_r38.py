from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    state = project / "state"
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(project / "home")
    environment["USERPROFILE"] = environment["HOME"]
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state),
            "--host",
            "codex",
            "fabric",
            "route",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
        env=environment,
    )


def _event(project: Path) -> dict[str, Any]:
    database = project / "state" / "competitive-fabric.sqlite3"
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT event_type,family,host,raw_bytes,visible_bytes,
                   success,cache_hit,metadata_json
            FROM fabric_events
            ORDER BY event_id
            """
        ).fetchone()
    assert row is not None
    value = dict(row)
    value["metadata_json"] = json.loads(value["metadata_json"])
    return value


@pytest.mark.parametrize(
    "arguments,expected_family,expected_mode,expected_success,expected_cache_hit",
    [
        (("--", "pytest", "-q"), "test", "background-replace", 1, 0),
        (
            ("--network-untrusted", "--", "curl", "https://example.com"),
            "network",
            "sandbox-replace",
            1,
            0,
        ),
        (("--", "git", "reset", "--hard"), "git", "blocked", 0, 0),
        (
            ("--repeated", "--", "rg", "token", "."),
            "search",
            "execute-and-capture",
            1,
            1,
        ),
    ],
)
def test_native_fabric_route_matches_python_and_sqlite_mutation(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_family: str,
    expected_mode: str,
    expected_success: int,
    expected_cache_hit: int,
) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, *arguments)
    rust = _run("rust", rust_project, *arguments)

    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    assert rust_value == python_value
    assert rust_value["family"] == expected_family
    assert rust_value["mode"] == expected_mode

    python_event = _event(python_project)
    rust_event = _event(rust_project)
    assert rust_event == python_event
    assert rust_event == {
        "event_type": "route",
        "family": expected_family,
        "host": "codex",
        "raw_bytes": 0,
        "visible_bytes": 0,
        "success": expected_success,
        "cache_hit": expected_cache_hit,
        "metadata_json": {
            "capture": bool(rust_value["capture_required"]),
            "mode": expected_mode,
        },
    }


def test_native_fabric_route_output_file_matches_python(tmp_path: Path) -> None:
    output = tmp_path / "route-result.json"
    python_project = tmp_path / "python-output-project"
    rust_project = tmp_path / "rust-output-project"

    python = _run(
        "python",
        python_project,
        "--output",
        str(output),
        "--",
        "pytest",
        "-q",
    )
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_value = json.loads(output.read_text(encoding="utf-8"))

    rust = _run(
        "rust",
        rust_project,
        "--output",
        str(output),
        "--",
        "pytest",
        "-q",
    )
    assert rust.returncode == 0 and rust.stderr == ""
    assert json.loads(rust.stdout) == python_receipt
    assert json.loads(output.read_text(encoding="utf-8")) == python_value
