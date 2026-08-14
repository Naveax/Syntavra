from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary
from tests.runtime.test_native_run_artifact_put_r38 import _run as _put_run
from tests.runtime.test_native_run_artifact_put_r38 import _value as _put_value

ROOT = Path(__file__).resolve().parents[2]


def _run_verify(
    engine: str,
    project: Path,
    state: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-artifact-verify-home"
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
            "artifact-verify",
            *extra,
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
    assert result.stderr == "", (result.stdout, result.stderr)
    return json.loads(result.stdout)


def _put(
    engine: str,
    project: Path,
    state: Path,
    payload: str,
    *,
    kind: str = "generic",
) -> dict[str, Any]:
    return _put_value(
        _put_run(
            engine,
            project,
            state,
            payload,
            "--kind",
            kind,
        )
    )


def _both(
    project: Path,
    state: Path,
    *extra: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    python_result = _run_verify("python", project, state, *extra)
    rust_result = _run_verify("rust", project, state, *extra)
    python = _value(python_result)
    rust = _value(rust_result)
    expected_exit = 0 if python.get("ok") is not False else 3
    assert python_result.returncode == expected_exit, (
        python_result.returncode,
        python_result.stdout,
        python_result.stderr,
    )
    assert rust_result.returncode == python_result.returncode, (
        rust_result.returncode,
        python_result.returncode,
        rust_result.stdout,
        rust_result.stderr,
    )
    assert rust == python
    return python, rust


def test_native_artifact_verify_empty_store_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    value, _ = _both(project, state)
    assert value == {"ok": True, "checked": 0, "failures": []}


def test_native_artifact_verify_healthy_all_and_specific_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first = _put("python", project, state, "alpha")
    second = _put("rust", project, state, "beta")

    all_value, _ = _both(project, state)
    assert all_value == {"ok": True, "checked": 2, "failures": []}

    selected, _ = _both(project, state, first["artifact_id"])
    assert selected == {"ok": True, "checked": 1, "failures": []}
    assert first["artifact_id"] != second["artifact_id"]


def test_native_artifact_verify_unknown_specific_id_is_empty_success_like_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    missing_id = "sha256:" + ("0" * 64)
    value, _ = _both(project, state, missing_id)
    assert value == {"ok": True, "checked": 0, "failures": []}


def test_native_artifact_verify_missing_object_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put("python", project, state, "missing-object")
    Path(record["object_path"]).unlink()

    value, _ = _both(project, state, record["artifact_id"])
    assert value == {
        "ok": False,
        "checked": 1,
        "failures": [f"missing:{record['artifact_id']}"],
    }


def test_native_artifact_verify_hash_failure_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put("rust", project, state, "original")
    Path(record["object_path"]).write_text("tampered", encoding="utf-8")

    value, _ = _both(project, state, record["artifact_id"])
    assert value == {
        "ok": False,
        "checked": 1,
        "failures": [f"hash:{record['artifact_id']}"],
    }


def test_native_artifact_verify_all_reports_multiple_failures_in_db_order(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    healthy = _put("python", project, state, "healthy")
    corrupt = _put("rust", project, state, "corrupt")
    missing = _put("python", project, state, "missing")

    Path(corrupt["object_path"]).write_text("changed", encoding="utf-8")
    Path(missing["object_path"]).unlink()

    value, _ = _both(project, state)
    assert value == {
        "ok": False,
        "checked": 3,
        "failures": [
            f"hash:{corrupt['artifact_id']}",
            f"missing:{missing['artifact_id']}",
        ],
    }
    assert healthy["artifact_id"] not in "\n".join(value["failures"])


def test_native_artifact_verify_rejects_unexpected_arguments_like_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    for engine in ("python", "rust"):
        result = _run_verify(engine, project, state, "one", "two")
        assert result.returncode != 0
