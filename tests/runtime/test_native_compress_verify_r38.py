from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.runtime.test_native_backup_create_r38 import _environment
from tests.runtime.test_native_compress_get_r38 import _run_rust_with_key
from tests.runtime.test_native_compress_put_r38 import _json, _run_compress
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run_with_key(
    engine: str,
    project: Path,
    state: Path,
    arguments: list[str],
    key: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-compress-verify-key-home"
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = _environment(home, None)
    environment["SYNTAVRA_EVIDENCE_KEY"] = key
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
        env=environment,
    )


def _assert_verify(
    completed: subprocess.CompletedProcess[str],
    compression_id: str,
    *,
    ok: bool,
) -> dict[str, Any]:
    assert completed.returncode == (0 if ok else 3), (
        completed.stdout,
        completed.stderr,
    )
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    assert value == {"compression_id": compression_id, "ok": ok}
    return value


def _assert_pair(
    project: Path,
    state: Path,
    compression_id: str,
    *,
    ok: bool,
) -> None:
    python_value = _assert_verify(
        _run_compress("python", project, state, ["verify", compression_id]),
        compression_id,
        ok=ok,
    )
    rust_value = _assert_verify(
        _run_compress("rust", project, state, ["verify", compression_id]),
        compression_id,
        ok=ok,
    )
    assert rust_value == python_value


@pytest.mark.parametrize("creator", ("python", "rust"))
def test_native_compress_verify_accepts_python_and_rust_created_binary_state(
    tmp_path: Path,
    creator: str,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / f"{creator}-state"
    payload = bytes(range(256)) * 600 + b"roundtrip-tail"
    source = tmp_path / f"{creator}-source.bin"
    source.write_bytes(payload)
    created = _json(
        _run_compress(
            creator,
            project,
            state,
            [
                "put",
                "--input",
                str(source),
                "--hint",
                "text",
                "--budget-bytes",
                "512",
            ],
        )
    )
    assert created["chunk_count"] == 3
    _assert_pair(project, state, created["compression_id"], ok=True)


@pytest.mark.parametrize(
    ("column", "value_sql"),
    (
        ("receipt_hash", "'" + "0" * 64 + "'"),
        ("original_bytes", "original_bytes + 1"),
        ("visible_text", "visible_text || 'tampered'"),
        ("chunk_size", "chunk_size + 1"),
    ),
)
def test_native_compress_verify_matches_python_for_receipt_contract_tampering(
    tmp_path: Path,
    column: str,
    value_sql: str,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / f"state-{column}"
    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text", "receipt contract payload β", "--hint", "text"],
        )
    )
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.execute(
            f"UPDATE compressions SET {column}={value_sql} WHERE compression_id=?",
            (created["compression_id"],),
        )
    _assert_pair(project, state, created["compression_id"], ok=False)


def test_native_compress_verify_detects_missing_chunk_row(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = b"a" * 70000
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    created = _json(
        _run_compress(
            "python",
            project,
            state,
            ["put", "--input", str(source), "--hint", "text"],
        )
    )
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.execute(
            "DELETE FROM compression_chunks WHERE compression_id=? AND chunk_index=1",
            (created["compression_id"],),
        )
    _assert_pair(project, state, created["compression_id"], ok=False)


def test_native_compress_verify_detects_exact_and_chunk_payload_divergence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text", "first exact payload", "--hint", "text"],
        )
    )
    second = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text", "different valid payload", "--hint", "text"],
        )
    )
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.execute(
            "UPDATE compressions SET exact_handle=? WHERE compression_id=?",
            (second["exact_handle"], first["compression_id"]),
        )
    _assert_pair(project, state, first["compression_id"], ok=False)


@pytest.mark.parametrize("target", ("exact", "chunk"))
def test_native_compress_verify_fails_closed_on_authenticated_object_corruption(
    tmp_path: Path,
    target: str,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / f"state-{target}"
    payload = bytes(range(256)) * 400
    source = tmp_path / f"source-{target}.bin"
    source.write_bytes(payload)
    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--input", str(source), "--hint", "text"],
        )
    )
    if target == "exact":
        handle = created["exact_handle"]
    else:
        described = _json(
            _run_compress(
                "python",
                project,
                state,
                ["describe", created["compression_id"]],
            )
        )
        handle = described["chunks"][0]["chunk_handle"]
    digest = handle.removeprefix("sc://sha256/")
    object_path = state / "evidence" / "objects" / digest[:2] / digest[2:]
    corrupted = bytearray(object_path.read_bytes())
    corrupted[-1] ^= 0x01
    object_path.write_bytes(corrupted)

    for engine in ("python", "rust"):
        completed = _run_compress(
            engine,
            project,
            state,
            ["verify", created["compression_id"]],
        )
        assert completed.returncode != 0


def test_native_compress_verify_fails_closed_with_wrong_managed_key(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first_key = "11" * 32
    second_key = "22" * 32
    created = _json(
        _run_rust_with_key(
            project,
            state,
            ["put", "--text", "managed verify payload", "--hint", "text"],
            first_key,
        )
    )
    for engine in ("python", "rust"):
        completed = _run_with_key(
            engine,
            project,
            state,
            ["verify", created["compression_id"]],
            second_key,
        )
        assert completed.returncode != 0


def test_native_compress_verify_rejects_missing_record_and_invalid_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    for engine in ("python", "rust"):
        missing = _run_compress(
            engine,
            project,
            state,
            ["verify", "ccr-does-not-exist"],
        )
        assert missing.returncode != 0

    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text", "metadata payload", "--hint", "text"],
        )
    )
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        connection.execute(
            "UPDATE compressions SET metadata_json='{' WHERE compression_id=?",
            (created["compression_id"],),
        )
    for engine in ("python", "rust"):
        malformed = _run_compress(
            engine,
            project,
            state,
            ["verify", created["compression_id"]],
        )
        assert malformed.returncode != 0
