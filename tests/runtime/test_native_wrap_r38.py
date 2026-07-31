from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _engine(engine: str, *arguments: str) -> Any:
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
        [*argv, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def test_native_wrapper_explicit_output_matches_python_exactly(tmp_path: Path) -> None:
    python_path = tmp_path / ("python.cmd" if os.name == "nt" else "python-wrapper")
    rust_path = tmp_path / ("rust.cmd" if os.name == "nt" else "rust-wrapper")
    python_result = _engine("python", "wrap", "codex", "--output", str(python_path))
    rust_result = _engine("rust", "wrap", "codex", "--output", str(rust_path))
    assert {**rust_result, "path": str(python_path)} == python_result
    assert rust_path.read_bytes() == python_path.read_bytes()
    if os.name != "nt":
        assert rust_path.stat().st_mode & stat.S_IXUSR
        assert python_path.stat().st_mode & stat.S_IXUSR


def test_native_wrapper_default_path_matches_python_contract(tmp_path: Path) -> None:
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_result = _engine(
        "python",
        "--state-root",
        str(python_state),
        "wrap",
        "claude-code",
    )
    rust_result = _engine(
        "rust",
        "--state-root",
        str(rust_state),
        "wrap",
        "claude-code",
    )
    expected_name = "claude-code.cmd" if os.name == "nt" else "claude-code"
    assert Path(python_result["path"]).name == Path(rust_result["path"]).name == expected_name
    assert Path(rust_result["path"]).read_bytes() == Path(python_result["path"]).read_bytes()
    assert rust_result["ok"] == python_result["ok"] is True
    assert rust_result["host"] == python_result["host"] == "claude-code"
    assert rust_result["version"] == python_result["version"] == "0.0.1"
