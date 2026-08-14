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
        timeout=240,
    )


def _assert_exact(*arguments: str) -> dict[str, object]:
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == 0
    assert rust_result.stderr == ""
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    return rust_value


def test_native_semantic_demo_matches_python_scores_and_impact() -> None:
    value = _assert_exact("semantic-demo", "demo", "auth refresh")
    assert value["results"][0]["node"]["node_id"] == "auth"  # type: ignore[index]
    assert value["impact"]["affected_tests"][0]["node_id"] == "test"  # type: ignore[index]


def test_native_structural_v2_matches_python_for_test_query() -> None:
    value = _assert_exact("structural-v2", "demo", "test_auth_refresh")
    assert value["results"][0]["node"]["node_id"] == "test"  # type: ignore[index]


def test_native_signalbench_default_plan_matches_python_manifest() -> None:
    value = _assert_exact("signalbench", "plan")
    assert value["corpus"]["tasks"] == 150  # type: ignore[index]
    assert value["schedule"]["runs"] == 27_000  # type: ignore[index]
    manifest_hash = value["manifest"]["manifest_hash"]  # type: ignore[index]
    assert isinstance(manifest_hash, str) and len(manifest_hash) == 64


def test_native_signalbench2_custom_repetitions_matches_python_manifest() -> None:
    value = _assert_exact("signalbench2", "plan", "--repetitions", "31")
    assert value["schedule"]["repetitions"] == 31  # type: ignore[index]
    assert value["schedule"]["runs"] == 27_900  # type: ignore[index]
