from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any
import json

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


def _python_engine(*arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ]
    )


def _rust_engine(*arguments: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            *arguments,
        ]
    )


def _seed_version(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE syntavra_schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,identity TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO syntavra_schema_migrations(version,name,identity,applied_at) VALUES(?,?,?,?)",
            (version, f"migration-{version}", "a" * 64, 1.0),
        )
        connection.execute("CREATE TABLE payload(value TEXT NOT NULL)")
        connection.execute("INSERT INTO payload(value) VALUES('original')")
        connection.commit()
    finally:
        connection.close()


def _payload(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [str(row[0]) for row in connection.execute("SELECT value FROM payload ORDER BY rowid")]
    finally:
        connection.close()


def test_native_migration_apply_missing_database_matches_python(tmp_path: Path) -> None:
    python_database = tmp_path / "python" / "missing.sqlite3"
    rust_database = tmp_path / "rust" / "missing.sqlite3"

    python_result = _python_engine("migrate", "apply", str(python_database), "--dry-run")
    rust_result = _rust_engine("migrate", "apply", str(rust_database), "--dry-run")

    assert {**rust_result, "database": str(python_database)} == python_result
    assert rust_result["before_version"] == 0
    assert rust_result["after_version"] == 0
    assert rust_result["applied"] == []
    assert not python_database.exists()
    assert not rust_database.exists()


def test_native_migration_apply_reads_existing_version_exactly(tmp_path: Path) -> None:
    python_database = tmp_path / "python.sqlite3"
    rust_database = tmp_path / "rust.sqlite3"
    _seed_version(python_database, 7)
    shutil.copy2(python_database, rust_database)

    python_result = _python_engine("migrate", "apply", str(python_database))
    rust_result = _rust_engine("migrate", "apply", str(rust_database))

    assert {**rust_result, "database": str(python_database)} == python_result
    assert rust_result["before_version"] == 7
    assert rust_result["after_version"] == 7
    assert rust_result["ok"] is True


def test_native_migration_rollback_restores_backup_bytes(tmp_path: Path) -> None:
    backup = tmp_path / "backup.sqlite3"
    _seed_version(backup, 4)
    python_database = tmp_path / "python" / "state.sqlite3"
    rust_database = tmp_path / "rust" / "state.sqlite3"
    python_database.parent.mkdir(parents=True)
    rust_database.parent.mkdir(parents=True)
    shutil.copy2(backup, python_database)
    shutil.copy2(backup, rust_database)

    for database in (python_database, rust_database):
        connection = sqlite3.connect(database)
        try:
            connection.execute("INSERT INTO payload(value) VALUES('mutated')")
            connection.commit()
        finally:
            connection.close()

    python_result = _python_engine(
        "migrate",
        "rollback",
        str(python_database),
        str(backup),
    )
    rust_result = _rust_engine(
        "migrate",
        "rollback",
        str(rust_database),
        str(backup),
    )

    assert rust_result == python_result == {"ok": True}
    assert _payload(python_database) == ["original"]
    assert _payload(rust_database) == ["original"]
    assert python_database.read_bytes() == backup.read_bytes()
    assert rust_database.read_bytes() == backup.read_bytes()
    assert not Path(f"{rust_database}-wal").exists()
    assert not Path(f"{rust_database}-shm").exists()
