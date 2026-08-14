from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def _python_engine(*arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ]
    )


def _rust_engine(*arguments: str) -> Any:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    return _json_command(
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
            *arguments,
        ]
    )


def test_native_provider_capabilities_match_python_exactly() -> None:
    arguments = ("provider", "capabilities")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_provider_alias_matches_python_exactly() -> None:
    arguments = ("provider", "capabilities", "claude")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_output_profiles_match_python_exactly() -> None:
    arguments = ("output", "profiles")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


@pytest.mark.parametrize("tier", ["1X", "20X", "30X", "100X"])
def test_native_benchmark_config_matches_python_exactly(
    tmp_path: Path,
    tier: str,
) -> None:
    python_output = tmp_path / f"python-{tier}.json"
    rust_output = tmp_path / f"rust-{tier}.json"
    python_result = _python_engine(
        "benchmark",
        "generate-config",
        "--tier",
        tier,
        "--output",
        str(python_output),
    )
    rust_result = _rust_engine(
        "benchmark",
        "generate-config",
        "--tier",
        tier,
        "--output",
        str(rust_output),
    )
    assert rust_result == python_result
    assert json.loads(rust_output.read_text(encoding="utf-8")) == json.loads(
        python_output.read_text(encoding="utf-8")
    )
