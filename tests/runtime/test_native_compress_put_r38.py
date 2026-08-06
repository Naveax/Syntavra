from __future__ import annotations

import copy
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.runtime.test_native_backup_create_r38 import _environment
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]
ID_RE = re.compile(r"ccr-[0-9a-f]{32}")


def _run_compress(
    engine: str,
    project: Path,
    state: Path,
    arguments: list[str],
    *,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-compress-put-home"
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
        input=stdin,
        timeout=600,
        env=_environment(home, None),
    )


def _json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def _normalized(value: dict[str, Any]) -> dict[str, Any]:
    rendered = copy.deepcopy(value)
    rendered.pop("compression_id")
    rendered.pop("receipt_hash")
    rendered["visible_text"] = ID_RE.sub("ccr-<id>", rendered["visible_text"])
    return rendered


def _verify_with_python(project: Path, state: Path, compression_id: str) -> None:
    completed = _run_compress(
        "python", project, state, ["verify", compression_id]
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "compression_id": compression_id,
        "ok": True,
    }


def _restore_with_python(
    project: Path,
    state: Path,
    compression_id: str,
    output: Path,
) -> bytes:
    value = _json(
        _run_compress(
            "python",
            project,
            state,
            ["get", compression_id, "--output", str(output)],
        )
    )
    assert value == {
        "compression_id": compression_id,
        "bytes": output.stat().st_size,
        "output": str(output),
    }
    return output.read_bytes()


CASES: tuple[tuple[str, list[str], str | None], ...] = (
    (
        "json",
        [
            "put",
            "--text",
            json.dumps(
                {
                    "alpha": list(range(16)),
                    "nested": {"enabled": True, "value": "exact"},
                    "password": "not-redacted-inside-quoted-json-key",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "--hint",
            "json",
            "--path",
            "fixtures/value.json",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "jsonl",
        [
            "put",
            "--text",
            '{"id":1,"ok":true}\ninvalid\n{"id":2,"ok":false}\n',
            "--path",
            "fixtures/events.jsonl",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "table",
        [
            "put",
            "--text",
            'name,value\nalpha,"one,two"\nbeta,three\ngamma,four\n',
            "--path",
            "fixtures/table.csv",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "log",
        [
            "put",
            "--text",
            "INFO request 100 ok\nERROR request 101 failed password=secret\nERROR request 102 failed password=other\n",
            "--hint",
            "log",
            "--path",
            "fixtures/runtime.log",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "stack",
        [
            "put",
            "--text",
            'Traceback failure\n  File "main.py", line 10\n  File "worker.py", line 22\nRuntimeError: denied\n',
            "--hint",
            "stack",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "diff",
        [
            "put",
            "--text",
            "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n",
            "--hint",
            "diff",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "xml",
        [
            "put",
            "--text",
            "<root><item>alpha</item><item>beta</item></root>",
            "--hint",
            "xml",
            "--path",
            "fixtures/value.xml",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "rag",
        [
            "put",
            "--text",
            "alpha beta gamma\n\nsmall\n\none two three four five\n\nred blue green yellow",
            "--hint",
            "rag",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "text",
        [
            "put",
            "--text",
            "First sentence. Second sentence! ERROR final sentence? password=hidden",
            "--hint",
            "text",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "code-fallback",
        [
            "put",
            "--text",
            "target(); caller();",
            "--hint",
            "code",
            "--budget-bytes",
            "4096",
        ],
        None,
    ),
    (
        "stdin",
        [
            "put",
            "--hint",
            "text",
            "--budget-bytes",
            "4096",
        ],
        "stdin exact payload. second segment.",
    ),
)


@pytest.mark.parametrize(("name", "arguments", "stdin"), CASES)
def test_native_compress_put_cross_engine_semantics(
    tmp_path: Path,
    name: str,
    arguments: list[str],
    stdin: str | None,
) -> None:
    project = tmp_path / "project"
    python_state = tmp_path / f"{name}-python-state"
    rust_state = tmp_path / f"{name}-rust-state"
    python_value = _json(
        _run_compress("python", project, python_state, arguments, stdin=stdin)
    )
    rust_value = _json(
        _run_compress("rust", project, rust_state, arguments, stdin=stdin)
    )
    assert _normalized(rust_value) == _normalized(python_value)
    assert rust_value["reversible"] is True
    assert rust_value["loss_policy"] == "exact-externalized"
    assert rust_value["exact_handle"].startswith("sc://sha256/")
    assert len(rust_value["receipt_hash"]) == 64
    int(rust_value["receipt_hash"], 16)
    _verify_with_python(project, rust_state, rust_value["compression_id"])


def test_native_compress_put_rust_source_summary_matches_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    arguments = [
        "put",
        "--text",
        "pub fn target() -> i32 { 1 }\npub fn caller() -> i32 { target() }\n",
        "--path",
        "src/main.rs",
        "--budget-bytes",
        "4096",
    ]
    python_value = _json(
        _run_compress("python", project, tmp_path / "python-state", arguments)
    )
    rust_state = tmp_path / "rust-state"
    rust_value = _json(_run_compress("rust", project, rust_state, arguments))
    assert _normalized(rust_value) == _normalized(python_value)
    assert rust_value["metadata"] == {
        "path": "src/main.rs",
        "hint": "",
        "language": "rust",
        "parser": "language-lexical-v3:rust",
        "symbols": 2,
        "edges": 5,
    }
    _verify_with_python(project, rust_state, rust_value["compression_id"])


def test_native_compress_put_python_reads_rust_evidence_and_description(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = b"password=secret\n" + bytes(range(256)) * 300
    source = tmp_path / "large-input.bin"
    source.write_bytes(payload)
    created = _json(
        _run_compress(
            "rust",
            project,
            state,
            [
                "put",
                "--input",
                str(source),
                "--hint",
                "text",
                "--budget-bytes",
                "256",
            ],
        )
    )
    assert created["original_bytes"] == len(payload)
    assert created["chunk_count"] == 2
    assert "password=<redacted>" in created["visible_text"]
    assert "password=secret" not in created["visible_text"]

    described = _json(
        _run_compress(
            "python", project, state, ["describe", created["compression_id"]]
        )
    )
    assert described["compression_id"] == created["compression_id"]
    assert described["content_type"] == created["content_type"]
    assert described["exact_handle"] == created["exact_handle"]
    assert described["chunk_count"] == 2
    assert [row["chunk_index"] for row in described["chunks"]] == [0, 1]
    assert [row["chunk_bytes"] for row in described["chunks"]] == [65536, len(payload) - 65536]

    restored = _restore_with_python(
        project,
        state,
        created["compression_id"],
        tmp_path / "restored.bin",
    )
    assert restored == payload
    _verify_with_python(project, state, created["compression_id"])

    digest = created["exact_handle"].removeprefix("sc://sha256/")
    object_path = state / "evidence" / "objects" / digest[:2] / digest[2:]
    assert object_path.read_bytes().startswith(b"SCEV1\0")
    assert payload not in object_path.read_bytes()


def test_native_compress_put_empty_payload_has_one_reversible_chunk(
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
    assert created["original_bytes"] == 0
    assert created["chunk_count"] == 1
    assert _restore_with_python(
        project,
        state,
        created["compression_id"],
        tmp_path / "empty.bin",
    ) == b""
    _verify_with_python(project, state, created["compression_id"])


def test_native_compress_put_tiny_budget_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    arguments = [
        "put",
        "--text",
        "payload",
        "--hint",
        "text",
        "--budget-bytes",
        "0",
    ]
    python_value = _json(
        _run_compress("python", project, tmp_path / "python-state", arguments)
    )
    rust_state = tmp_path / "rust-state"
    rust_value = _json(_run_compress("rust", project, rust_state, arguments))
    assert _normalized(rust_value) == _normalized(python_value)
    assert rust_value["visible_text"] == (
        "\n[visible view truncated; use CCR handle for exact restoration]"
    )
    _verify_with_python(project, rust_state, rust_value["compression_id"])


def test_native_compress_put_invalid_json_fails_before_evidence_write(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    completed = _run_compress(
        "rust",
        project,
        state,
        ["put", "--text", "{", "--hint", "json"],
    )
    assert completed.returncode != 0
    assert (state / "compression.sqlite3").is_file()
    assert (state / "evidence" / "evidence.sqlite3").is_file()
    with sqlite3.connect(state / "compression.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM compressions").fetchone()[0] == 0
    with sqlite3.connect(state / "evidence" / "evidence.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_objects").fetchone()[0] == 0
