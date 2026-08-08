from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
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


def _engine_argv(
    engine: str,
    project: Path,
    state_root: Path,
    *arguments: str,
) -> list[str]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return [
        *prefix,
        "--engine",
        engine,
        "--project",
        str(project),
        "--state-root",
        str(state_root),
        *arguments,
    ]


def _object_path(state_root: Path, digest: str) -> Path:
    return state_root / "evidence" / "objects" / digest[:2] / digest[2:]


def _metadata_path(state_root: Path, digest: str) -> Path:
    return state_root / "evidence" / "metadata" / f"{digest}.json"


def _prepare_state(state_root: Path) -> dict[str, str]:
    evidence = state_root / "evidence"
    (evidence / "keys").mkdir(parents=True)
    (evidence / "objects").mkdir()
    (evidence / "metadata").mkdir()
    (evidence / "keys" / "active.json").write_text(
        json.dumps({"schema_version": 1, "active_version": 1}),
        encoding="utf-8",
    )
    (evidence / "keys" / "master-v1.key").write_bytes(b"k" * 32)

    digests = {
        "expired-small": "a" * 64,
        "expired-large": "b" * 64,
        "referenced": "c" * 64,
        "held": "d" * 64,
        "fresh": "e" * 64,
    }
    database = sqlite3.connect(evidence / "evidence.sqlite3")
    try:
        database.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE evidence_objects(
                digest TEXT PRIMARY KEY,
                plaintext_bytes INTEGER NOT NULL,
                stored_bytes INTEGER NOT NULL,
                key_version INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                expires_at REAL,
                ref_count INTEGER NOT NULL DEFAULT 0,
                legal_hold INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE evidence_references(
                digest TEXT NOT NULL,
                reference TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(digest,reference),
                FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE
            );
            CREATE INDEX evidence_expiry_idx ON evidence_objects(expires_at);
            """
        )
        database.executemany(
            "INSERT INTO evidence_objects VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (digests["expired-small"], 80, 100, 1, 1.0, 1.0, 1.0, 0, 0),
                (digests["expired-large"], 160, 200, 1, 2.0, 2.0, 2.0, 0, 0),
                (digests["referenced"], 240, 300, 1, 3.0, 3.0, 3.0, 1, 0),
                (digests["held"], 320, 400, 1, 4.0, 4.0, 4.0, 0, 1),
                (
                    digests["fresh"],
                    400,
                    500,
                    1,
                    4_102_444_800.0,
                    4_102_444_800.0,
                    4_102_444_800.0,
                    0,
                    0,
                ),
            ],
        )
        database.execute(
            "INSERT INTO evidence_references VALUES(?,?,?)",
            (digests["referenced"], "fixture-reference", 3.0),
        )
        database.commit()
    finally:
        database.close()

    for label, digest in digests.items():
        object_path = _object_path(state_root, digest)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(f"object:{label}".encode())
        _metadata_path(state_root, digest).write_text(
            json.dumps({"schema_version": 3, "digest": digest, "label": label}),
            encoding="utf-8",
        )
    return digests


def _state_pair(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    project = tmp_path / "project"
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    digests = _prepare_state(source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return project, python_state, rust_state, digests


def _remaining(state_root: Path) -> list[str]:
    database = sqlite3.connect(state_root / "evidence" / "evidence.sqlite3")
    try:
        return [
            row[0]
            for row in database.execute(
                "SELECT digest FROM evidence_objects ORDER BY digest"
            )
        ]
    finally:
        database.close()


def test_native_evidence_gc_dry_run_matches_python_without_mutation(tmp_path: Path) -> None:
    project, python_state, rust_state, digests = _state_pair(tmp_path)
    arguments = ("evidence", "gc", "--ttl-days", "30")

    python_result = _json_command(
        _engine_argv("python", project, python_state, *arguments)
    )
    rust_result = _json_command(_engine_argv("rust", project, rust_state, *arguments))

    assert rust_result == python_result == {
        "ok": True,
        "dry_run": True,
        "objects": 2,
        "deleted": 0,
        "plaintext_bytes": 240,
        "stored_bytes": 300,
        "bytes_reclaimed": 0,
    }
    expected = sorted(digests.values())
    assert _remaining(python_state) == expected
    assert _remaining(rust_state) == expected
    for state_root in (python_state, rust_state):
        for digest in digests.values():
            assert _object_path(state_root, digest).is_file()
            assert _metadata_path(state_root, digest).is_file()


def test_native_evidence_gc_apply_matches_python_and_removes_files(tmp_path: Path) -> None:
    project, python_state, rust_state, digests = _state_pair(tmp_path)
    arguments = ("evidence", "gc", "--ttl-days", "30", "--apply")

    python_result = _json_command(
        _engine_argv("python", project, python_state, *arguments)
    )
    rust_result = _json_command(_engine_argv("rust", project, rust_state, *arguments))

    assert rust_result == python_result == {
        "ok": True,
        "dry_run": False,
        "objects": 2,
        "deleted": 2,
        "plaintext_bytes": 240,
        "stored_bytes": 300,
        "bytes_reclaimed": 300,
    }
    removed = {digests["expired-small"], digests["expired-large"]}
    expected_remaining = sorted(set(digests.values()) - removed)
    for state_root in (python_state, rust_state):
        assert _remaining(state_root) == expected_remaining
        for digest in removed:
            assert not _object_path(state_root, digest).exists()
            assert not _metadata_path(state_root, digest).exists()
        for digest in expected_remaining:
            assert _object_path(state_root, digest).is_file()
            assert _metadata_path(state_root, digest).is_file()


def test_native_janitor_byte_budget_matches_python(tmp_path: Path) -> None:
    project, python_state, rust_state, digests = _state_pair(tmp_path)
    arguments = (
        "maintenance",
        "janitor",
        "--ttl-days",
        "30",
        "--max-delete-bytes",
        "150",
        "--apply",
    )

    python_result = _json_command(
        _engine_argv("python", project, python_state, *arguments)
    )
    rust_result = _json_command(_engine_argv("rust", project, rust_state, *arguments))

    assert rust_result == python_result == {
        "ok": True,
        "dry_run": False,
        "objects": 1,
        "deleted": 1,
        "plaintext_bytes": 80,
        "stored_bytes": 100,
        "bytes_reclaimed": 100,
    }
    removed = digests["expired-small"]
    for state_root in (python_state, rust_state):
        assert removed not in _remaining(state_root)
        assert not _object_path(state_root, removed).exists()
        assert not _metadata_path(state_root, removed).exists()
        assert digests["expired-large"] in _remaining(state_root)


def test_native_janitor_zero_budget_selects_nothing(tmp_path: Path) -> None:
    project, python_state, rust_state, _ = _state_pair(tmp_path)
    arguments = (
        "maintenance",
        "janitor",
        "--max-delete-bytes=0",
        "--ttl-days=30",
    )
    assert _json_command(_engine_argv("rust", project, rust_state, *arguments)) == _json_command(
        _engine_argv("python", project, python_state, *arguments)
    ) == {
        "ok": True,
        "dry_run": True,
        "objects": 0,
        "deleted": 0,
        "plaintext_bytes": 0,
        "stored_bytes": 0,
        "bytes_reclaimed": 0,
    }
