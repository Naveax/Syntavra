from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _first_difference, _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    *arguments: str,
    host: str = "codex",
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
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
            str(project / "state"),
            "--host",
            host,
            "fabric",
            "profile",
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


def _snapshot(project: Path) -> dict[str, Any]:
    database = project / "state" / "competitive-fabric.sqlite3"
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        indexes = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%' "
                "ORDER BY name"
            )
        ]
        metadata = dict(connection.execute("SELECT key,value FROM metadata ORDER BY key"))
        events = connection.execute("SELECT COUNT(*) FROM fabric_events").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "tables": tables,
        "indexes": indexes,
        "metadata": metadata,
        "events": events,
        "integrity": integrity,
    }


def _pair(
    tmp_path: Path,
    *arguments: str,
    host: str = "codex",
) -> tuple[dict[str, Any], Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, *arguments, host=host)
    rust = _run("rust", rust_project, *arguments, host=host)
    assert rust.returncode == python.returncode == 0, {
        "arguments": arguments,
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    difference = _first_difference(rust_value, python_value)
    assert difference is None, difference
    assert _snapshot(rust_project) == _snapshot(python_project)
    return rust_value, python_project, rust_project


def test_native_fabric_profile_auto_minimal_matches_python(tmp_path: Path) -> None:
    value, _, _ = _pair(tmp_path, "--task", "fix typo")
    assert value["profile"] == "minimal"
    assert value["available_count"] == 93
    assert value["host"] == "codex"
    assert value["host_mode"] == "MCP_CONTROLLED"
    assert value["selected_count"] == len(value["selected_tools"])
    assert value["omitted_count"] == 93 - value["selected_count"]
    assert value["selected_tools"][0] == "syntavra.status"
    assert "syntavra.inspect.map" in value["selected_tools"]
    assert "syntavra.output.capture" in value["selected_tools"]
    assert "syntavra.fabric.profile" in value["selected_tools"]
    assert "syntavra.fabric.insights" in value["selected_tools"]


def test_native_fabric_profile_auto_balanced_expands_intents(tmp_path: Path) -> None:
    value, _, _ = _pair(tmp_path, "--task", "test repository logs")
    assert value["profile"] == "balanced"
    assert "syntavra.process.submit" in value["selected_tools"]
    assert "syntavra.inspect.impact" in value["selected_tools"]
    assert "syntavra.output.verify" in value["selected_tools"]
    assert value["selected_count"] > 30
    assert value["within_budget"] is True


def test_native_fabric_profile_aliases_match_python(tmp_path: Path) -> None:
    cases = (
        ("tiny", "minimal"),
        ("optimized", "balanced"),
        ("full", "audit"),
    )
    for requested, expected in cases:
        value, _, _ = _pair(
            tmp_path / requested,
            "--task",
            "audit the complete tool surface",
            "--profile",
            requested,
        )
        assert value["profile"] == expected
        if expected == "audit":
            assert value["selected_count"] == 93
            assert value["omitted_count"] == 0
            assert value["within_budget"] is True


def test_native_fabric_profile_repeated_profile_uses_last_value(tmp_path: Path) -> None:
    value, _, _ = _pair(
        tmp_path,
        "--task",
        "fix typo",
        "--profile",
        "full",
        "--profile",
        "tiny",
    )
    assert value["profile"] == "minimal"


def test_native_fabric_profile_unknown_host_mode_matches_python(tmp_path: Path) -> None:
    value, _, _ = _pair(
        tmp_path,
        "--task",
        "fix typo",
        host="unknown-agent",
    )
    assert value["host"] == "unknown-agent"
    assert value["host_mode"] == "UNSUPPORTED"


def test_native_fabric_profile_invalid_arguments_fail_before_database(tmp_path: Path) -> None:
    for suffix, arguments in (
        ("missing-task", ()),
        ("invalid-profile", ("--task", "fix typo", "--profile", "balanced")),
    ):
        for engine in ("python", "rust"):
            project = tmp_path / suffix / f"{engine}-project"
            completed = _run(engine, project, *arguments)
            assert completed.returncode != 0
            assert not (project / "state" / "competitive-fabric.sqlite3").exists()


def test_native_fabric_profile_output_receipt_matches_payload(tmp_path: Path) -> None:
    output = tmp_path / "profile.json"
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"

    python = _run(
        "python",
        python_project,
        "--task",
        "test repository logs",
        "--output",
        str(output),
    )
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_payload = output.read_bytes()
    python_value = json.loads(python_payload)

    rust = _run(
        "rust",
        rust_project,
        "--task",
        "test repository logs",
        "--output",
        str(output),
    )
    assert rust.returncode == 0 and rust.stderr == ""
    rust_receipt = json.loads(rust.stdout)
    rust_payload = output.read_bytes()
    rust_value = json.loads(rust_payload)
    assert python_receipt["ok"] is rust_receipt["ok"] is True
    assert python_receipt["output"] == rust_receipt["output"] == str(output)
    assert python_receipt["bytes"] == len(python_payload)
    assert rust_receipt["bytes"] == len(rust_payload)
    difference = _first_difference(rust_value, python_value)
    assert difference is None, difference
    assert _snapshot(rust_project) == _snapshot(python_project)
