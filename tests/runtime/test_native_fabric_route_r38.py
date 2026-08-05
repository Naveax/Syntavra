from __future__ import annotations

import hashlib
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


def _run_cache_align(
    engine: str,
    project: Path,
    *arguments: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
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
            "cache-align",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        input=input_text,
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


def test_native_fabric_cache_align_matches_python_and_sqlite_mutation(
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "stable ünicode",
                    "request_id": "volatile-request",
                    "_debug": True,
                    "nested": {
                        "keep": "exact",
                        "timestamp": 123.5,
                        "trace_id": "volatile-trace",
                    },
                },
                {
                    "role": "user",
                    "content": "tail",
                    "usage": {"input_tokens": 4},
                },
            ]
        },
        ensure_ascii=False,
    )
    arguments = (
        "--keep-tail",
        "0",
        "--keep-tail",
        "1",
        "--payload",
        json.dumps({"messages": []}),
        "--payload",
        payload,
    )
    python_project = tmp_path / "python-cache-project"
    rust_project = tmp_path / "rust-cache-project"
    python = _run_cache_align("python", python_project, *arguments)
    rust = _run_cache_align("rust", rust_project, *arguments)

    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    assert rust_value == python_value

    canonical = json.dumps(
        [
            {
                "content": "stable ünicode",
                "nested": {"keep": "exact"},
                "role": "system",
            }
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert rust_value == {
        "cacheable_bytes": len(canonical.encode("utf-8")),
        "canonical_prefix": canonical,
        "prefix_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "stable_message_count": 1,
        "volatile_fields": ["_debug", "request_id", "timestamp", "trace_id"],
        "volatile_tail_count": 1,
    }

    python_event = _event(python_project)
    rust_event = _event(rust_project)
    assert rust_event == python_event
    assert rust_event == {
        "event_type": "cache-align",
        "family": "provider-request",
        "host": "codex",
        "raw_bytes": rust_value["cacheable_bytes"],
        "visible_bytes": rust_value["cacheable_bytes"],
        "success": 1,
        "cache_hit": 0,
        "metadata_json": {"stable_messages": 1},
    }


def test_native_fabric_cache_align_input_stdin_and_output_match_python(
    tmp_path: Path,
) -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "one", "updated_at": 1},
            {"role": "user", "content": "two", "response_id": "volatile"},
        ]
    }
    source = tmp_path / "cache-request.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "cache-result.json"

    python_project = tmp_path / "python-cache-output-project"
    rust_project = tmp_path / "rust-cache-output-project"
    python = _run_cache_align(
        "python",
        python_project,
        "--input",
        str(source),
        "--keep-tail",
        "0",
        "--output",
        str(output),
    )
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_value = json.loads(output.read_text(encoding="utf-8"))

    rust = _run_cache_align(
        "rust",
        rust_project,
        "--input=-",
        "--keep-tail=0",
        f"--output={output}",
        input_text=json.dumps(payload),
    )
    assert rust.returncode == 0 and rust.stderr == ""
    assert json.loads(rust.stdout) == python_receipt
    assert json.loads(output.read_text(encoding="utf-8")) == python_value
    assert _event(rust_project) == _event(python_project)
