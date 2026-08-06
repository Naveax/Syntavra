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


def _run(
    engine: str,
    project: Path,
    state: Path,
    command: list[str],
    *,
    key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-evidence-get-home"
    home.mkdir(parents=True, exist_ok=True)
    env = _environment(home, None)
    if key is not None:
        env["SYNTAVRA_EVIDENCE_KEY"] = key
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
            *command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=env,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def _create(
    engine: str,
    project: Path,
    state: Path,
    payload: bytes,
    *,
    key: str | None = None,
) -> str:
    source = state.parent / f"{state.name}-{engine}-source.bin"
    source.write_bytes(payload)
    value = _json(
        _run(
            engine,
            project,
            state,
            ["compress", "put", "--input", str(source), "--hint", "text"],
            key=key,
        )
    )
    return str(value["exact_handle"])


def _digest(handle: str) -> str:
    return handle.removeprefix("sc://sha256/")


def _metadata(state: Path, handle: str) -> Path:
    return state / "evidence" / "metadata" / f"{_digest(handle)}.json"


def _object(state: Path, handle: str) -> Path:
    digest = _digest(handle)
    return state / "evidence" / "objects" / digest[:2] / digest[2:]


def _accessed(state: Path, handle: str) -> float:
    with sqlite3.connect(state / "evidence" / "evidence.sqlite3") as db:
        row = db.execute(
            "SELECT last_accessed_at FROM evidence_objects WHERE digest=?",
            (_digest(handle),),
        ).fetchone()
    assert row is not None
    return float(row[0])


def test_native_evidence_get_text_json_parity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = "alpha βeta\nreplacement: �\n".encode()
    handle = _create("python", project, state, payload)
    expected = {"handle": handle, "bytes": len(payload), "text": payload.decode()}
    assert _json(_run("python", project, state, ["evidence", "get", handle])) == expected
    assert _json(_run("rust", project, state, ["evidence", "get", handle])) == expected


def test_native_evidence_get_binary_output_and_repeated_options(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = bytes(range(256)) * 700 + b"tail"
    handle = _create("rust", project, state, payload)
    for engine in ("python", "rust"):
        first = tmp_path / f"{engine}-first.bin"
        second = tmp_path / f"{engine}-second.bin"
        second.write_bytes(b"stale")
        value = _json(
            _run(
                engine,
                project,
                state,
                [
                    "evidence",
                    "get",
                    "--max-bytes",
                    "1",
                    handle,
                    "--max-bytes",
                    str(len(payload)),
                    "--output",
                    str(first),
                    "--output",
                    str(second),
                ],
            )
        )
        assert value == {"handle": handle, "bytes": len(payload), "output": str(second)}
        assert not first.exists()
        assert second.read_bytes() == payload
        assert _json(
            _run(engine, project, state, ["evidence", "get", handle, "--output="])
        )["bytes"] == len(payload)


def test_native_evidence_get_limits_fail_before_access_mutation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = b"bounded exact evidence"
    handle = _create("python", project, state, payload)
    before = _accessed(state, handle)
    for engine in ("python", "rust"):
        output = tmp_path / f"{engine}-limited.bin"
        result = _run(
            engine,
            project,
            state,
            ["evidence", "get", handle, "--max-bytes", str(len(payload) - 1), "--output", str(output)],
        )
        assert result.returncode != 0
        assert not output.exists()
        assert _accessed(state, handle) == before

    path = _metadata(state, handle)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["bytes"] = 0
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for engine in ("python", "rust"):
        result = _run(engine, project, state, ["evidence", "get", handle, "--max-bytes", "0"])
        assert result.returncode != 0
        assert _accessed(state, handle) == before


def test_native_evidence_get_rejects_invalid_missing_scope_and_schema(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    assert _run("rust", project, state, ["evidence", "get", "invalid"]).returncode != 0
    assert (state / "evidence" / "evidence.sqlite3").is_file()
    missing = "sc://sha256/" + "a" * 64
    for engine in ("python", "rust"):
        assert _run(engine, project, state, ["evidence", "get", missing]).returncode != 0

    handle = _create("python", project, state, b"scoped")
    other = tmp_path / "other-project"
    for engine in ("python", "rust"):
        assert _run(engine, other, state, ["evidence", "get", handle]).returncode != 0

    path = _metadata(state, handle)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema_version"] = 2
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for engine in ("python", "rust"):
        assert _run(engine, project, state, ["evidence", "get", handle]).returncode != 0


def test_native_evidence_get_fails_closed_on_authentication_errors(tmp_path: Path) -> None:
    project = tmp_path / "project"
    corrupt_state = tmp_path / "corrupt-state"
    handle = _create("rust", project, corrupt_state, b"authenticated evidence")
    path = _object(corrupt_state, handle)
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    for engine in ("python", "rust"):
        output = tmp_path / f"{engine}-corrupt.bin"
        result = _run(engine, project, corrupt_state, ["evidence", "get", handle, "--output", str(output)])
        assert result.returncode != 0
        assert not output.exists()

    key_state = tmp_path / "key-state"
    first_key = "11" * 32
    handle = _create("rust", project, key_state, b"managed evidence", key=first_key)
    for engine in ("python", "rust"):
        output = tmp_path / f"{engine}-wrong-key.bin"
        result = _run(
            engine,
            project,
            key_state,
            ["evidence", "get", handle, "--output", str(output)],
            key="22" * 32,
        )
        assert result.returncode != 0
        assert not output.exists()
