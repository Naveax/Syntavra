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


def _run_stats(
    engine: str,
    project: Path,
    state: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-artifact-stats-home"
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
            "artifact-stats",
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
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
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


def _both(project: Path, state: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    python = _value(_run_stats("python", project, state))
    rust = _value(_run_stats("rust", project, state))
    assert rust == python
    return python, rust


def test_native_artifact_stats_empty_store_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    value, _ = _both(project, state)
    assert value == {"artifacts": 0, "exact_bytes": 0, "kinds": []}


def test_native_artifact_stats_counts_bytes_and_sorted_kinds_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    _put("python", project, state, "aaa", kind="zeta")
    _put("rust", project, state, "bbbb", kind="alpha")
    _put("python", project, state, "cc", kind="alpha")

    value, _ = _both(project, state)
    assert value == {
        "artifacts": 3,
        "exact_bytes": 9,
        "kinds": [
            {"kind": "alpha", "count": 2, "bytes": 6},
            {"kind": "zeta", "count": 1, "bytes": 3},
        ],
    }


def test_native_artifact_stats_dedup_preserves_single_metadata_row(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    first = _put("python", project, state, "same", kind="first")
    second = _put("rust", project, state, "same", kind="second")
    assert second == first

    value, _ = _both(project, state)
    assert value == {
        "artifacts": 1,
        "exact_bytes": 4,
        "kinds": [{"kind": "first", "count": 1, "bytes": 4}],
    }


def test_native_artifact_stats_is_metadata_only_like_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put("rust", project, state, "original", kind="corruptible")
    Path(record["object_path"]).write_text("tampered", encoding="utf-8")

    value, _ = _both(project, state)
    assert value == {
        "artifacts": 1,
        "exact_bytes": len("original".encode()),
        "kinds": [
            {
                "kind": "corruptible",
                "count": 1,
                "bytes": len("original".encode()),
            }
        ],
    }


def test_native_artifact_stats_rejects_unexpected_arguments_like_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    for engine in ("python", "rust"):
        result = _run_stats(engine, project, state, "unexpected")
        assert result.returncode != 0
