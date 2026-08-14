from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        [*argv, "prove", "suites"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_native_external_suite_manifest_matches_python_exactly() -> None:
    python_result = _run("python")
    rust_result = _run("rust")
    assert python_result.returncode == rust_result.returncode == 0, (
        python_result.stdout,
        python_result.stderr,
        rust_result.stdout,
        rust_result.stderr,
    )
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    assert rust_value["suite_count"] == 5
    assert rust_value["suites"][0]["suite_id"] == "swe-bench"
    assert rust_value["suites"][-1]["suite_id"] == "recursive-long-context"
    assert len(rust_value["manifest_hash"]) == 64
