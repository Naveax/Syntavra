from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    *arguments: str,
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
            "insights",
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


def _database(project: Path) -> Path:
    return project / "state" / "competitive-fabric.sqlite3"


def _snapshot(project: Path) -> dict[str, Any]:
    database = _database(project)
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
        event_count = connection.execute("SELECT COUNT(*) FROM fabric_events").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "tables": tables,
        "indexes": indexes,
        "metadata": metadata,
        "event_count": event_count,
        "integrity": integrity,
    }


def _seed(project: Path, rows: list[tuple[Any, ...]]) -> None:
    database = _database(project)
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO fabric_events(
                event_type,family,host,raw_bytes,visible_bytes,latency_ms,
                success,cache_hit,metadata_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        connection.commit()


def _assert_equal(
    tmp_path: Path,
    *arguments: str,
) -> tuple[dict[str, Any], Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, *arguments)
    rust = _run("rust", rust_project, *arguments)
    assert rust.returncode == python.returncode == 0, {
        "arguments": arguments,
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    assert rust_value == python_value
    assert _snapshot(rust_project) == _snapshot(python_project)
    return rust_value, python_project, rust_project


def _canonical_rows(now: float) -> list[tuple[Any, ...]]:
    return [
        ("route", "code", "codex", 100, 80, 10.0, 1, 0, "{}", now - 3600),
        ("compact", "test", "codex", 300, 120, 20.0, 0, 1, "{}", now - 20),
        ("route", "code", "claude-code", 400, 200, 30.0, 1, 0, "{}", now - 10),
        ("cache-align", "provider-request", "codex", 200, 200, 40.0, 1, 1, "{}", now - 5),
    ]


def test_native_fabric_insights_empty_ledger_matches_python(tmp_path: Path) -> None:
    value, _, _ = _assert_equal(tmp_path)
    assert value == {
        "events": 0,
        "success_rate": 1.0,
        "cache_hit_rate": 0.0,
        "raw_bytes": 0,
        "visible_bytes": 0,
        "saved_bytes": 0,
        "savings_ratio": 0.0,
        "latency_ms": {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0},
        "families": {},
        "event_types": {},
        "hosts": {},
        "database_integrity": True,
    }


def test_native_fabric_insights_seeded_metrics_match_python(tmp_path: Path) -> None:
    _, python_project, rust_project = _assert_equal(tmp_path / "bootstrap")
    rows = _canonical_rows(time.time())
    _seed(python_project, rows)
    _seed(rust_project, rows)

    python = _run("python", python_project)
    rust = _run("rust", rust_project)
    assert rust.returncode == python.returncode == 0
    value = json.loads(python.stdout)
    assert json.loads(rust.stdout) == value
    assert value == {
        "events": 4,
        "success_rate": 0.75,
        "cache_hit_rate": 0.5,
        "raw_bytes": 1000,
        "visible_bytes": 600,
        "saved_bytes": 400,
        "savings_ratio": 0.4,
        "latency_ms": {"mean": 25.0, "p50": 20.0, "p95": 40.0, "max": 40.0},
        "families": {"code": 2, "test": 1, "provider-request": 1},
        "event_types": {"route": 2, "compact": 1, "cache-align": 1},
        "hosts": {"codex": 3, "claude-code": 1},
        "database_integrity": True,
    }
    assert _snapshot(rust_project) == _snapshot(python_project)


def test_native_fabric_insights_since_filter_and_repeated_option_match_python(
    tmp_path: Path,
) -> None:
    _, python_project, rust_project = _assert_equal(tmp_path / "filter-bootstrap")
    rows = _canonical_rows(time.time())
    _seed(python_project, rows)
    _seed(rust_project, rows)

    arguments = ("--since-seconds", "1", "--since-seconds", "60")
    python = _run("python", python_project, *arguments)
    rust = _run("rust", rust_project, *arguments)
    assert rust.returncode == python.returncode == 0
    value = json.loads(python.stdout)
    assert json.loads(rust.stdout) == value
    assert value["events"] == 3
    assert value["raw_bytes"] == 900
    assert value["visible_bytes"] == 520
    assert value["latency_ms"] == {
        "mean": 30.0,
        "p50": 30.0,
        "p95": 40.0,
        "max": 40.0,
    }
    assert _snapshot(rust_project) == _snapshot(python_project)


def test_native_fabric_insights_output_receipt_matches_python(tmp_path: Path) -> None:
    output = tmp_path / "insights.json"
    python_project = tmp_path / "output-python"
    rust_project = tmp_path / "output-rust"

    python = _run("python", python_project, "--output", str(output))
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_value = json.loads(output.read_text(encoding="utf-8"))

    rust = _run("rust", rust_project, "--output", str(output))
    assert rust.returncode == 0 and rust.stderr == ""
    assert json.loads(rust.stdout) == python_receipt
    assert json.loads(output.read_text(encoding="utf-8")) == python_value
    assert _snapshot(rust_project) == _snapshot(python_project)
