from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    *,
    host: str = "codex",
    output: Path | None = None,
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
    arguments = [
        *prefix,
        "--engine",
        engine,
        "--project",
        str(project),
        "--state-root",
        str(state),
        "--host",
        host,
        "fabric",
        "doctor",
    ]
    if output is not None:
        arguments.extend(["--output", str(output)])
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
        env=environment,
    )


def _database_snapshot(project: Path) -> dict[str, Any]:
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


def _assert_equal(tmp_path: Path, *, host: str) -> dict[str, Any]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, host=host)
    rust = _run("rust", rust_project, host=host)
    assert rust.returncode == python.returncode == 0, {
        "host": host,
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    assert rust_value == python_value
    assert _database_snapshot(rust_project) == _database_snapshot(python_project)
    return rust_value


def test_native_fabric_doctor_codex_matches_python_and_state(tmp_path: Path) -> None:
    value = _assert_equal(tmp_path, host="codex")
    assert value["ok"] is True
    assert value["host"] == "codex"
    assert value["checks"]["analytics_database"] is True
    assert value["checks"]["known_host"] is True
    assert value["checks"]["mcp_available"] is True
    assert value["checks"]["result_replacement"] is False
    assert value["checks"]["enforced_mode"] is True
    assert value["checks"]["platform_registry_size"] == 44
    assert value["negotiation"]["mode"] == "MCP_CONTROLLED"
    assert value["profile_names"] == [
        "audit",
        "balanced",
        "full",
        "minimal",
        "optimized",
        "tiny",
    ]
    assert value["limitations"][0] == "result_replacement"


def test_native_fabric_doctor_unknown_host_matches_python(tmp_path: Path) -> None:
    value = _assert_equal(tmp_path, host="unknown-agent")
    assert value["ok"] is False
    assert value["checks"]["known_host"] is False
    assert value["checks"]["mcp_available"] is False
    assert value["checks"]["enforced_mode"] is False
    assert value["negotiation"]["mode"] == "UNSUPPORTED"
    assert value["negotiation"]["capabilities"]["host"] == "unknown-agent"
    assert value["limitations"][:2] == ["result_replacement", "enforced_mode"]


def test_native_fabric_doctor_case_sensitive_registry_check_matches_python(
    tmp_path: Path,
) -> None:
    value = _assert_equal(tmp_path, host="Codex")
    assert value["checks"]["known_host"] is False
    assert value["checks"]["mcp_available"] is True
    assert value["negotiation"]["mode"] == "MCP_CONTROLLED"
    assert value["ok"] is False


def test_native_fabric_doctor_output_receipt_matches_python(tmp_path: Path) -> None:
    output = tmp_path / "doctor.json"
    python_project = tmp_path / "output-python"
    rust_project = tmp_path / "output-rust"

    python = _run("python", python_project, output=output)
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_value = json.loads(output.read_text(encoding="utf-8"))

    rust = _run("rust", rust_project, output=output)
    assert rust.returncode == 0 and rust.stderr == ""
    assert json.loads(rust.stdout) == python_receipt
    assert json.loads(output.read_text(encoding="utf-8")) == python_value
    assert _database_snapshot(rust_project) == _database_snapshot(python_project)
