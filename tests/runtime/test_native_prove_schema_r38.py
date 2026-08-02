from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, output: Path) -> subprocess.CompletedProcess[str]:
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
        [*argv, "prove", "schema", "--output", str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_native_prove_schema_matches_python_json_and_file_bytes(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "provider-usage-receipt-v1.json"

    python_result = _run("python", output)
    assert python_result.returncode == 0, (python_result.stdout, python_result.stderr)
    python_value = json.loads(python_result.stdout)
    python_bytes = output.read_bytes()

    output.unlink()
    rust_result = _run("rust", output)
    assert rust_result.returncode == 0, (rust_result.stdout, rust_result.stderr)
    rust_value = json.loads(rust_result.stdout)
    rust_bytes = output.read_bytes()

    assert rust_value == python_value
    assert rust_bytes == python_bytes
    assert rust_bytes.endswith(b"\n")
    assert rust_value["ok"] is True
    assert rust_value["output"] == str(output)
    assert rust_value["schema"]["$id"] == (
        "https://syntavra.dev/schemas/provider-usage-receipt-v1.json"
    )
    assert rust_value["schema"]["additionalProperties"] is True
