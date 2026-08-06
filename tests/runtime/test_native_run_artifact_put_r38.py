from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    state: Path,
    input_value: str,
    *options: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-artifact-home"
    home.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        HOME=str(home),
        USERPROFILE=str(home),
        PATH="",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
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
            "run",
            "artifact-put",
            input_value,
            *options,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _value(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def _normalize(value: dict[str, Any], state: Path) -> dict[str, Any]:
    output = dict(value)
    output["created_at"] = "<created-at>"
    output["object_path"] = str(output["object_path"]).replace(str(state), "<state>")
    return output


def _database_rows(state: Path) -> list[tuple[Any, ...]]:
    path = state / "unified" / "artifacts" / "artifacts.sqlite3"
    with sqlite3.connect(path) as db:
        return db.execute(
            """
            SELECT artifact_id,sha256,media_type,kind,byte_count,
                   created_at,object_path,metadata_json
            FROM artifacts ORDER BY artifact_id
            """
        ).fetchall()


def _object_files(state: Path) -> list[Path]:
    root = state / "unified" / "artifacts" / "objects"
    return sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []


def _assert_record(value: dict[str, Any], payload: bytes) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    assert value["artifact_id"] == f"sha256:{digest}"
    assert value["sha256"] == digest
    assert value["byte_count"] == len(payload)
    assert value["metadata"] == {}
    path = Path(value["object_path"])
    assert path.read_bytes() == payload
    assert path.parts[-3:] == (digest[:2], digest[2:4], digest)


def test_native_artifact_put_inline_text_matches_python(tmp_path: Path) -> None:
    payload = "alpha βeta\nline two"
    values: dict[str, dict[str, Any]] = {}
    states: dict[str, Path] = {}
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = tmp_path / f"{engine}-state"
        states[engine] = state
        values[engine] = _value(
            _run(
                engine,
                project,
                state,
                payload,
                "--kind",
                "report",
                "--media-type",
                "text/markdown",
            )
        )
        _assert_record(values[engine], payload.encode())
        assert values[engine]["kind"] == "report"
        assert values[engine]["media_type"] == "text/markdown"
        assert len(_database_rows(state)) == len(_object_files(state)) == 1
    assert _normalize(values["rust"], states["rust"]) == _normalize(
        values["python"], states["python"]
    )


def test_native_artifact_put_file_replacement_and_newlines_match_python(
    tmp_path: Path,
) -> None:
    raw = b"first\r\ninvalid:\xff\rfinal\n"
    expected = "first\ninvalid:�\nfinal\n".encode()
    normalized: dict[str, dict[str, Any]] = {}
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = tmp_path / f"{engine}-state"
        source = project / "payload.txt"
        project.mkdir(parents=True)
        source.write_bytes(raw)
        value = _value(_run(engine, project, state, str(source)))
        _assert_record(value, expected)
        normalized[engine] = _normalize(value, state)
    assert normalized["rust"] == normalized["python"]


def test_native_artifact_put_python_first_dedup_preserves_first_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first = _value(
        _run(
            "python",
            project,
            state,
            "same payload",
            "--kind",
            "first-kind",
            "--media-type",
            "application/first",
        )
    )
    second = _value(
        _run(
            "rust",
            project,
            state,
            "same payload",
            "--kind",
            "second-kind",
            "--media-type",
            "application/second",
        )
    )
    assert second == first
    assert first["kind"] == "first-kind"
    assert first["media_type"] == "application/first"
    assert len(_database_rows(state)) == len(_object_files(state)) == 1


def test_native_artifact_put_rust_first_dedup_is_python_readable(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first = _value(
        _run(
            "rust",
            project,
            state,
            "same payload",
            "--kind=rust-first",
            "--media-type=application/rust",
        )
    )
    second = _value(
        _run(
            "python",
            project,
            state,
            "same payload",
            "--kind=python-second",
            "--media-type=application/python",
        )
    )
    assert second == first
    assert first["kind"] == "rust-first"
    assert first["media_type"] == "application/rust"
    assert json.loads(_database_rows(state)[0][-1]) == {}


def test_native_artifact_put_repeated_options_use_last_value(tmp_path: Path) -> None:
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = tmp_path / f"{engine}-state"
        value = _value(
            _run(
                engine,
                project,
                state,
                "options",
                "--kind",
                "first",
                "--kind",
                "last",
                "--media-type",
                "application/first",
                "--media-type",
                "application/last",
            )
        )
        assert value["kind"] == "last"
        assert value["media_type"] == "application/last"


def test_native_artifact_put_empty_payload_matches_python(tmp_path: Path) -> None:
    values: list[dict[str, Any]] = []
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = tmp_path / f"{engine}-state"
        value = _value(_run(engine, project, state, ""))
        _assert_record(value, b"")
        values.append(_normalize(value, state))
    assert values[0] == values[1]


def test_native_artifact_put_database_corruption_fails_closed(tmp_path: Path) -> None:
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = tmp_path / f"{engine}-state"
        root = state / "unified" / "artifacts"
        root.mkdir(parents=True)
        (root / "artifacts.sqlite3").write_bytes(b"not-a-sqlite-database")
        result = _run(engine, project, state, "payload")
        assert result.returncode != 0
        assert not _object_files(state)
