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
from tests.runtime.test_native_run_adapter_conformance_r38 import _state_shape

ROOT = Path(__file__).resolve().parents[2]
STATUS_ACTIONS = ("platform-status", "competitive-status")
DOCTOR_ACTIONS = ("platform-doctor", "competitive-doctor")


def _run(
    engine: str,
    project: Path,
    action: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = project.parent / f"{project.name}-home"
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
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
            "run",
            action,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _normalize(value: Any, project: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item, project) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, project) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<project>")
    return value


def _pair(
    tmp_path: Path,
    action: str,
    *,
    expected_code: int = 0,
    prepare: bool = False,
    corrupt: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    python_project = tmp_path / f"python-{action}"
    rust_project = tmp_path / f"rust-{action}"
    if prepare:
        _write_artifact(python_project, corrupt=corrupt)
        _write_artifact(rust_project, corrupt=corrupt)
    python = _run("python", python_project, action)
    rust = _run("rust", rust_project, action)
    assert rust.returncode == python.returncode == expected_code, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    assert _normalize(rust_value, rust_project) == _normalize(
        python_value,
        python_project,
    )
    return rust_value, python_value, rust_project, python_project


def _write_artifact(project: Path, *, corrupt: bool) -> None:
    project.mkdir(parents=True, exist_ok=True)
    root = project / "state" / "unified" / "artifacts"
    payload = b"native platform health artifact\n"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = root / "objects" / digest[:2] / digest[2:4] / digest
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(b"corrupt\n" if corrupt else payload)
    database = root / "artifacts.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                media_type TEXT NOT NULL,
                kind TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                object_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_kind
            ON artifacts(kind, created_at);
            """
        )
        connection.execute(
            """INSERT OR REPLACE INTO artifacts
               (artifact_id,sha256,media_type,kind,byte_count,created_at,object_path,metadata_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                f"sha256:{digest}",
                digest,
                "text/plain",
                "health-fixture",
                len(payload),
                "2026-08-06T00:00:00+00:00",
                str(object_path),
                "{}",
            ),
        )


def test_native_platform_status_aliases_match_python_fresh_state(tmp_path: Path) -> None:
    values = []
    for action in STATUS_ACTIONS:
        rust, python, rust_project, python_project = _pair(tmp_path, action)
        assert rust["product"] == python["product"] == "Syntavra"
        assert rust["version"] == python["version"] == "0.0.1"
        assert rust["channel"] == python["channel"] == "pre-release"
        assert rust["artifacts"] == {"artifacts": 0, "exact_bytes": 0, "kinds": []}
        assert rust["runtime_evidence"] == {
            "ok": True,
            "nodes": 0,
            "edges": 0,
            "relations": [],
        }
        assert _state_shape(rust_project) == _state_shape(python_project)
        values.append(_normalize(rust, rust_project))
    assert values[0] == values[1]


def test_native_platform_doctor_aliases_match_python_fresh_state(tmp_path: Path) -> None:
    values = []
    for action in DOCTOR_ACTIONS:
        rust, python, rust_project, python_project = _pair(tmp_path, action)
        assert rust["ok"] is python["ok"] is True
        assert rust["artifact_integrity"] == {
            "ok": True,
            "checked": 0,
            "failures": [],
        }
        assert rust["version_locked"] is True
        assert _state_shape(rust_project) == _state_shape(python_project)
        values.append(rust)
    assert values[0] == values[1]


def test_native_platform_status_reports_populated_artifact_stats(tmp_path: Path) -> None:
    rust, python, rust_project, python_project = _pair(
        tmp_path,
        "platform-status",
        prepare=True,
    )
    assert rust["artifacts"] == python["artifacts"] == {
        "artifacts": 1,
        "exact_bytes": 32,
        "kinds": [{"kind": "health-fixture", "count": 1, "bytes": 32}],
    }
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_platform_doctor_fails_closed_on_corrupt_artifact(tmp_path: Path) -> None:
    rust, python, rust_project, python_project = _pair(
        tmp_path,
        "competitive-doctor",
        expected_code=3,
        prepare=True,
        corrupt=True,
    )
    assert rust["ok"] is python["ok"] is False
    assert rust["artifact_integrity"] == python["artifact_integrity"]
    assert rust["artifact_integrity"]["checked"] == 1
    assert len(rust["artifact_integrity"]["failures"]) == 1
    assert rust["artifact_integrity"]["failures"][0].startswith("hash:sha256:")
    assert _state_shape(rust_project) == _state_shape(python_project)
