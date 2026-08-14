from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, *arguments: str) -> subprocess.CompletedProcess[str]:
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
        [*argv, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_native_openai_proxy_plan_matches_python_exactly() -> None:
    arguments = ("run", "proxy-plan", "openai")
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == 0
    assert json.loads(rust_result.stdout) == json.loads(python_result.stdout)


def test_native_proxy_plan_failure_matches_python_output_and_exit_code() -> None:
    arguments = ("run", "proxy-plan", "azure-openai")
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == 3
    assert json.loads(rust_result.stdout) == json.loads(python_result.stdout)


def test_native_proxy_plan_custom_upstream_matches_python_exactly() -> None:
    arguments = (
        "run",
        "proxy-plan",
        "azure-openai",
        "--upstream",
        "https://example.openai.azure.com",
    )
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == 0
    assert json.loads(rust_result.stdout) == json.loads(python_result.stdout)
