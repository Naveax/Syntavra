from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_backup_create_r38 import _environment
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run_compress(
    engine: str,
    project: Path,
    state: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-compress-home"
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state),
            "compress",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=_environment(home, None),
    )


def _json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def _initialize_manual_database(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE compressions(
                compression_id TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                exact_handle TEXT NOT NULL,
                original_bytes INTEGER NOT NULL,
                visible_text TEXT NOT NULL,
                chunk_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                receipt_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE compression_chunks(
                compression_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_handle TEXT NOT NULL,
                chunk_bytes INTEGER NOT NULL,
                PRIMARY KEY(compression_id,chunk_index),
                FOREIGN KEY(compression_id) REFERENCES compressions(compression_id) ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO compressions(
                compression_id,content_type,exact_handle,original_bytes,visible_text,
                chunk_size,chunk_count,metadata_json,receipt_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "ccr-deterministic",
                "json",
                "evidence-exact-handle",
                70001,
                "visible αβγ\nsecond line",
                65536,
                2,
                json.dumps(
                    {
                        "hint": "json",
                        "nested": {"enabled": True, "values": [1, 2, 3]},
                        "path": "fixtures/input.json",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "a" * 64,
                1777777777.125,
            ),
        )
        connection.executemany(
            """
            INSERT INTO compression_chunks(
                compression_id,chunk_index,chunk_handle,chunk_bytes
            ) VALUES(?,?,?,?)
            """,
            [
                ("ccr-deterministic", 1, "evidence-chunk-1", 4465),
                ("ccr-deterministic", 0, "evidence-chunk-0", 65536),
            ],
        )


def test_native_compress_describe_python_record_matches_exactly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    created = _json(
        _run_compress(
            "python",
            project,
            state,
            [
                "put",
                "--text",
                '{"records":[1,2,3],"password":"do-not-expose"}',
                "--hint",
                "json",
                "--path",
                "fixtures/sample.json",
                "--budget-bytes",
                "4096",
            ],
        )
    )
    compression_id = created["compression_id"]
    python_value = _json(
        _run_compress("python", project, state, ["describe", compression_id])
    )
    rust_value = _json(
        _run_compress("rust", project, state, ["describe", compression_id])
    )
    assert rust_value == python_value
    assert rust_value["compression_id"] == compression_id
    assert rust_value["chunk_count"] == len(rust_value["chunks"])
    assert [row["chunk_index"] for row in rust_value["chunks"]] == list(
        range(rust_value["chunk_count"])
    )


def test_native_compress_describe_deterministic_row_and_chunk_order(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "manual-state"
    _initialize_manual_database(state)
    python_value = _json(
        _run_compress(
            "python", project, state, ["describe", "ccr-deterministic"]
        )
    )
    rust_value = _json(
        _run_compress("rust", project, state, ["describe", "ccr-deterministic"])
    )
    assert rust_value == python_value
    assert rust_value == {
        "compression_id": "ccr-deterministic",
        "content_type": "json",
        "exact_handle": "evidence-exact-handle",
        "original_bytes": 70001,
        "visible_text": "visible αβγ\nsecond line",
        "chunk_size": 65536,
        "chunk_count": 2,
        "receipt_hash": "a" * 64,
        "created_at": 1777777777.125,
        "metadata": {
            "hint": "json",
            "nested": {"enabled": True, "values": [1, 2, 3]},
            "path": "fixtures/input.json",
        },
        "chunks": [
            {
                "compression_id": "ccr-deterministic",
                "chunk_index": 0,
                "chunk_handle": "evidence-chunk-0",
                "chunk_bytes": 65536,
            },
            {
                "compression_id": "ccr-deterministic",
                "chunk_index": 1,
                "chunk_handle": "evidence-chunk-1",
                "chunk_bytes": 4465,
            },
        ],
    }


def test_native_compress_describe_missing_id_initializes_native_foundation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "empty-state"
    completed = _run_compress(
        "rust", project, state, ["describe", "ccr-missing"]
    )
    assert completed.returncode != 0
    assert (state / "compression.sqlite3").is_file()
    assert (state / "evidence" / "evidence.sqlite3").is_file()
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"compressions", "compression_chunks"}.issubset(tables)


def test_native_compress_describe_rejects_invalid_metadata_json(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "invalid-state"
    _initialize_manual_database(state)
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.execute(
            "UPDATE compressions SET metadata_json='{' WHERE compression_id=?",
            ("ccr-deterministic",),
        )
    for engine in ("python", "rust"):
        completed = _run_compress(
            engine, project, state, ["describe", "ccr-deterministic"]
        )
        assert completed.returncode != 0
