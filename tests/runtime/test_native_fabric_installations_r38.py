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


def _skill(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text("# Syntavra\n", encoding="utf-8", newline="\n")
    return root


def _run(
    engine: str,
    project: Path,
    skill_root: Path,
    *arguments: str,
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    selected_home = home or project / "home"
    selected_home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(selected_home)
    environment["USERPROFILE"] = str(selected_home)
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
            "codex",
            "fabric",
            "installations",
            "--skill-root",
            str(skill_root),
            "--home",
            str(selected_home),
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
    return project / "state" / "host-installations.sqlite3"


def _normalize(value: Any, *, project: Path, home: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item, project=project, home=home) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, project=project, home=home) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<PROJECT>").replace(str(home), "<HOME>")
    return value


def _assert_equal(
    rust: Any,
    python: Any,
    *,
    rust_project: Path,
    python_project: Path,
    rust_home: Path,
    python_home: Path,
) -> None:
    rust_value = _normalize(rust, project=rust_project, home=rust_home)
    python_value = _normalize(python, project=python_project, home=python_home)
    difference = _first_difference(rust_value, python_value)
    assert difference is None, difference


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
        count = connection.execute("SELECT COUNT(*) FROM host_install_transactions").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "tables": tables,
        "indexes": indexes,
        "metadata": metadata,
        "count": count,
        "integrity": integrity,
    }


def _seed(project: Path, home: Path) -> None:
    rows = [
        ("tx-old", "codex", "project", str(project), "applied", "{}", 10.0, 11.0),
        ("tx-mid", "cursor", "user", str(home), "rolled-back", "{}", 20.0, 21.0),
        ("tx-new", "codex", "project", str(project), "applied", "{}", 30.0, 31.0),
    ]
    with sqlite3.connect(_database(project)) as connection:
        connection.executemany(
            """
            INSERT INTO host_install_transactions(
                transaction_id,host,scope,root,status,manifest_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        connection.commit()


def test_native_fabric_installations_empty_state_matches_python(tmp_path: Path) -> None:
    skill = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    python = _run("python", python_project, skill, home=python_home)
    rust = _run("rust", rust_project, skill, home=rust_home)
    assert rust.returncode == python.returncode == 0
    assert rust.stderr == python.stderr == ""
    assert json.loads(rust.stdout) == json.loads(python.stdout) == []
    assert _snapshot(rust_project) == _snapshot(python_project)
    assert (rust_project / "state" / "host-installations").is_dir()
    assert (python_project / "state" / "host-installations").is_dir()


def test_native_fabric_installations_filters_orders_and_clamps_like_python(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    for engine, project, home in (
        ("python", python_project, python_home),
        ("rust", rust_project, rust_home),
    ):
        completed = _run(engine, project, skill, home=home)
        assert completed.returncode == 0
        _seed(project, home)

    cases = (
        (("--limit", "2"), ["tx-new", "tx-mid"]),
        (("--host-name", "CoDeX", "--limit", "20"), ["tx-new", "tx-old"]),
        (("--limit", "1", "--limit", "2"), ["tx-new", "tx-mid"]),
        (("--limit", "0"), ["tx-new"]),
        (("--limit", "999"), ["tx-new", "tx-mid", "tx-old"]),
    )
    for arguments, expected in cases:
        python = _run("python", python_project, skill, *arguments, home=python_home)
        rust = _run("rust", rust_project, skill, *arguments, home=rust_home)
        assert rust.returncode == python.returncode == 0
        assert rust.stderr == python.stderr == ""
        python_value = json.loads(python.stdout)
        rust_value = json.loads(rust.stdout)
        _assert_equal(
            rust_value,
            python_value,
            rust_project=rust_project,
            python_project=python_project,
            rust_home=rust_home,
            python_home=python_home,
        )
        assert [row["transaction_id"] for row in rust_value] == expected


def test_native_fabric_installations_output_receipt_matches_payload(tmp_path: Path) -> None:
    skill = _skill(tmp_path / "skill")
    output = tmp_path / "installations.json"
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    for engine, project, home in (
        ("python", python_project, python_home),
        ("rust", rust_project, rust_home),
    ):
        completed = _run(engine, project, skill, home=home)
        assert completed.returncode == 0
        _seed(project, home)

    python = _run(
        "python",
        python_project,
        skill,
        "--limit",
        "2",
        "--output",
        str(output),
        home=python_home,
    )
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_payload = output.read_bytes()
    python_value = json.loads(python_payload)

    rust = _run(
        "rust",
        rust_project,
        skill,
        "--limit",
        "2",
        "--output",
        str(output),
        home=rust_home,
    )
    assert rust.returncode == 0 and rust.stderr == ""
    rust_receipt = json.loads(rust.stdout)
    rust_payload = output.read_bytes()
    rust_value = json.loads(rust_payload)
    assert python_receipt["ok"] is rust_receipt["ok"] is True
    assert python_receipt["output"] == rust_receipt["output"] == str(output)
    assert python_receipt["bytes"] == len(python_payload)
    assert rust_receipt["bytes"] == len(rust_payload)
    _assert_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
    )


def test_native_fabric_installations_missing_skill_directory_fails_before_state(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-skill"
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        completed = _run(engine, project, missing)
        assert completed.returncode != 0
        assert not _database(project).exists()
        assert not (project / "state" / "host-installations").exists()


def test_native_fabric_installations_incomplete_skill_fails_after_state_init(
    tmp_path: Path,
) -> None:
    incomplete = tmp_path / "incomplete-skill"
    incomplete.mkdir()
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        completed = _run(engine, project, incomplete)
        assert completed.returncode != 0
        assert _database(project).is_file()
        assert (project / "state" / "host-installations").is_dir()
        assert _snapshot(project)["count"] == 0
