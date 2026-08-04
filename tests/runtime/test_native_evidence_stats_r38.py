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


def _python_engine(state_root: Path, *arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--state-root",
            str(state_root),
            *arguments,
        ]
    )


def _rust_engine(state_root: Path, *arguments: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--state-root",
            str(state_root),
            *arguments,
        ]
    )


def _prepare_evidence_store(state_root: Path) -> None:
    evidence = state_root / "evidence"
    keys = evidence / "keys"
    keys.mkdir(parents=True)
    (keys / "active.json").write_text(
        json.dumps({"schema_version": 1, "active_version": 3}, sort_keys=True),
        encoding="utf-8",
    )
    (keys / "master-v3.key").write_bytes(b"r38-native-evidence-key-32-bytes")
    database = sqlite3.connect(evidence / "evidence.sqlite3")
    try:
        database.executescript(
            """
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
                ("a" * 64, 100, 140, 3, 10.0, 10.0, 1.0, 0, 0),
                ("b" * 64, 200, 240, 3, 10.0, 10.0, 1.0, 2, 0),
                ("c" * 64, 300, 340, 3, 10.0, 10.0, 1.0, 0, 1),
                ("d" * 64, 400, 440, 3, 10.0, 10.0, 4_102_444_800.0, 1, 0),
            ],
        )
        database.commit()
    finally:
        database.close()


def _prepare_runtime_evidence(state_root: Path) -> None:
    state_root.mkdir(parents=True)
    database = sqlite3.connect(state_root / "runtime-evidence.sqlite3")
    try:
        database.executescript(
            """
            CREATE TABLE nodes(
                node_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                repository_commit TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE edges(
                evidence TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL,
                repository_commit TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX idx_evidence_source ON edges(source,relation);
            CREATE INDEX idx_evidence_target ON edges(target,relation);
            """
        )
        database.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?)",
            [
                ("node-a", "function", "a", "src/a.py", 1.0, "commit-a", "{}"),
                ("node-b", "test", "b", "tests/b.py", 0.9, "commit-a", "{}"),
                ("node-c", "file", "c", "src/c.py", 0.8, "commit-a", "{}"),
            ],
        )
        database.executemany(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?)",
            [
                ("ev-1", "node-b", "node-a", "CALLS", 1.0, "commit-a", "2026-08-03T00:00:00+00:00", "{}"),
                ("ev-2", "node-c", "node-a", "CALLS", 0.8, "commit-a", "2026-08-03T00:00:01+00:00", "{}"),
                ("ev-3", "node-b", "node-c", "COVERS", 0.9, "commit-a", "2026-08-03T00:00:02+00:00", "{}"),
            ],
        )
        database.commit()
    finally:
        database.close()


def _runtime_state_pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    _prepare_runtime_evidence(source / "unified")
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return python_state, rust_state


def test_native_empty_evidence_store_stats_match_python(tmp_path: Path) -> None:
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    assert _rust_engine(rust_state, "evidence", "stats") == _python_engine(
        python_state, "evidence", "stats"
    )


def test_native_populated_evidence_store_stats_match_python(tmp_path: Path) -> None:
    source = tmp_path / "source"
    python_state = tmp_path / "python"
    rust_state = tmp_path / "rust"
    _prepare_evidence_store(source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)

    rust_result = _rust_engine(rust_state, "evidence", "stats")
    python_result = _python_engine(python_state, "evidence", "stats")

    assert rust_result == python_result
    assert rust_result == {
        "active_key_version": 3,
        "collectable": 1,
        "encrypted": True,
        "objects": 4,
        "plaintext_bytes": 1000,
        "references": 3,
        "stored_bytes": 1160,
    }


def test_native_runtime_evidence_stats_match_python(tmp_path: Path) -> None:
    python_state, rust_state = _runtime_state_pair(tmp_path)

    rust_result = _rust_engine(rust_state, "run", "evidence-stats")
    python_result = _python_engine(python_state, "run", "evidence-stats")

    assert rust_result == python_result
    assert rust_result == {
        "ok": True,
        "nodes": 3,
        "edges": 3,
        "relations": [
            {"relation": "CALLS", "count": 2},
            {"relation": "COVERS", "count": 1},
        ],
    }


def test_native_runtime_evidence_forward_neighbors_match_python(tmp_path: Path) -> None:
    python_state, rust_state = _runtime_state_pair(tmp_path)
    rust_result = _rust_engine(rust_state, "run", "evidence-neighbors", "node-b")
    python_result = _python_engine(python_state, "run", "evidence-neighbors", "node-b")

    assert rust_result == python_result
    assert [row["evidence"] for row in rust_result["neighbors"]] == ["ev-3", "ev-1"]
    assert [row["node"]["node_id"] for row in rust_result["neighbors"]] == ["node-c", "node-a"]
    assert rust_result["neighbors"][0]["metadata"] == {}
    assert rust_result["neighbors"][0]["node"]["metadata_json"] == "{}"


def test_native_runtime_evidence_relation_filter_matches_python(tmp_path: Path) -> None:
    python_state, rust_state = _runtime_state_pair(tmp_path)
    arguments = ("run", "evidence-neighbors", "node-b", "--relation", "CALLS")
    rust_result = _rust_engine(rust_state, *arguments)
    python_result = _python_engine(python_state, *arguments)

    assert rust_result == python_result
    assert [row["evidence"] for row in rust_result["neighbors"]] == ["ev-1"]


def test_native_runtime_evidence_reverse_neighbors_match_python(tmp_path: Path) -> None:
    python_state, rust_state = _runtime_state_pair(tmp_path)
    arguments = ("run", "evidence-neighbors", "node-a", "--reverse")
    rust_result = _rust_engine(rust_state, *arguments)
    python_result = _python_engine(python_state, *arguments)

    assert rust_result == python_result
    assert [row["evidence"] for row in rust_result["neighbors"]] == ["ev-2", "ev-1"]
    assert [row["node"]["node_id"] for row in rust_result["neighbors"]] == ["node-c", "node-b"]
