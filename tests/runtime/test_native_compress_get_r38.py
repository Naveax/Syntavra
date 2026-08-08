from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_backup_create_r38 import _environment
from tests.runtime.test_native_compress_put_r38 import _json, _run_compress
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]
CHUNK_SIZE = 64 * 1024


def _run_rust_with_key(
    project: Path,
    state: Path,
    arguments: list[str],
    key: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-rust-compress-get-key-home"
    home.mkdir(parents=True, exist_ok=True)
    environment = _environment(home, None)
    environment["SYNTAVRA_EVIDENCE_KEY"] = key
    return subprocess.run(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
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


def _assert_output_value(
    value: dict[str, Any], compression_id: str, output: Path, expected: bytes
) -> None:
    assert value == {
        "compression_id": compression_id,
        "bytes": len(expected),
        "output": str(output),
    }
    assert output.read_bytes() == expected


def test_native_compress_get_reads_python_text_with_exact_json_parity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = "alpha βeta\ninvalid-byte-view: �\n"
    created = _json(
        _run_compress(
            "python",
            project,
            state,
            ["put", "--text", payload, "--hint", "text", "--budget-bytes", "4096"],
        )
    )
    python_value = _json(
        _run_compress("python", project, state, ["get", created["compression_id"]])
    )
    rust_value = _json(
        _run_compress("rust", project, state, ["get", created["compression_id"]])
    )
    assert rust_value == python_value == {
        "compression_id": created["compression_id"],
        "bytes": len(payload.encode("utf-8")),
        "text": payload,
    }


def test_native_compress_get_restores_python_binary_and_ordered_chunks(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = bytes(range(256)) * 600 + b"tail"
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    created = _json(
        _run_compress(
            "python",
            project,
            state,
            ["put", "--input", str(source), "--hint", "text", "--budget-bytes", "512"],
        )
    )
    assert created["chunk_count"] == 3

    full_output = tmp_path / "full.bin"
    full_output.write_bytes(b"stale-content-that-must-be-truncated")
    full = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["get", created["compression_id"], "--output", str(full_output)],
        )
    )
    _assert_output_value(full, created["compression_id"], full_output, payload)

    for index in range(created["chunk_count"]):
        expected = payload[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
        rust_output = tmp_path / f"rust-chunk-{index}.bin"
        python_output = tmp_path / f"python-chunk-{index}.bin"
        rust_value = _json(
            _run_compress(
                "rust",
                project,
                state,
                [
                    "get",
                    created["compression_id"],
                    "--chunk",
                    str(index),
                    "--output",
                    str(rust_output),
                ],
            )
        )
        python_value = _json(
            _run_compress(
                "python",
                project,
                state,
                [
                    "get",
                    created["compression_id"],
                    "--chunk",
                    str(index),
                    "--output",
                    str(python_output),
                ],
            )
        )
        _assert_output_value(rust_value, created["compression_id"], rust_output, expected)
        _assert_output_value(python_value, created["compression_id"], python_output, expected)


def test_native_compress_get_reads_rust_created_empty_payload(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text=", "--hint", "text", "--budget-bytes", "4096"],
        )
    )
    rust_value = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["get", created["compression_id"], "--chunk", "0"],
        )
    )
    python_value = _json(
        _run_compress(
            "python",
            project,
            state,
            ["get", created["compression_id"], "--chunk", "0"],
        )
    )
    assert rust_value == python_value == {
        "compression_id": created["compression_id"],
        "bytes": 0,
        "text": "",
    }


def test_native_compress_get_rejects_missing_and_invalid_chunks_without_output(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    missing_output = tmp_path / "missing.bin"
    missing = _run_compress(
        "rust",
        project,
        state,
        ["get", "ccr-does-not-exist", "--output", str(missing_output)],
    )
    assert missing.returncode != 0
    assert not missing_output.exists()

    created = _json(
        _run_compress(
            "rust", project, state, ["put", "--text", "payload", "--hint", "text"]
        )
    )
    for chunk in ("-1", "1"):
        output = tmp_path / f"invalid-{chunk}.bin"
        completed = _run_compress(
            "rust",
            project,
            state,
            [
                "get",
                created["compression_id"],
                "--chunk",
                chunk,
                "--output",
                str(output),
            ],
        )
        assert completed.returncode != 0
        assert not output.exists()


def test_native_compress_get_fails_closed_on_corrupted_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            ["put", "--text", "integrity payload", "--hint", "text"],
        )
    )
    digest = created["exact_handle"].removeprefix("sc://sha256/")
    object_path = state / "evidence" / "objects" / digest[:2] / digest[2:]
    corrupted = bytearray(object_path.read_bytes())
    corrupted[-1] ^= 0x01
    object_path.write_bytes(corrupted)

    output = tmp_path / "corrupt.bin"
    completed = _run_compress(
        "rust",
        project,
        state,
        ["get", created["compression_id"], "--output", str(output)],
    )
    assert completed.returncode != 0
    assert not output.exists()


def test_native_compress_get_fails_closed_with_wrong_managed_key(
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
            ["put", "--text", "managed secret payload", "--hint", "text"],
            first_key,
        )
    )
    output = tmp_path / "wrong-key.bin"
    completed = _run_rust_with_key(
        project,
        state,
        ["get", created["compression_id"], "--output", str(output)],
        second_key,
    )
    assert completed.returncode != 0
    assert not output.exists()
