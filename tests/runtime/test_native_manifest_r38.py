from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _engine(engine: str, project: Path) -> Any:
    if engine == "rust" and shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    argv = (
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "--bin",
            "syntavra",
            "--",
            "--engine",
            "rust",
        ]
        if engine == "rust"
        else [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
        ]
    )
    completed = subprocess.run(
        [*argv, "--project", str(project), "run", "manifest"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "engine": engine,
        "project": str(project),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def test_native_manifest_matches_python_for_repository() -> None:
    assert _engine("rust", ROOT) == _engine("python", ROOT)


def test_native_manifest_matches_python_for_empty_project(tmp_path: Path) -> None:
    project = tmp_path / "empty-project"
    project.mkdir()
    rust = _engine("rust", project)
    python = _engine("python", project)
    assert rust == python
    assert rust["competitive_features"]["ok"] is False
    assert all(value is False for value in rust["competitive_features"]["artifacts"].values())
