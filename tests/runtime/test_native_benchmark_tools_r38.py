from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.benchmark_harness import write_config

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bins"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    selector = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert selector.is_file(), selector
    return selector


def _run(engine: str, *arguments: str) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [*prefix, "--engine", engine, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _without_score(value: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    copied = json.loads(json.dumps(value))
    score = float(copied["difficulty"].pop("score"))
    return score, copied


def test_native_benchmark_validate_config_matches_python(tmp_path: Path) -> None:
    config = tmp_path / "benchmark-config.json"
    write_config(config, "30X")

    python_code, python_result = _run(
        "python", "benchmark", "validate-config", "--config", str(config)
    )
    rust_code, rust_result = _run(
        "rust", "benchmark", "validate-config", "--config", str(config)
    )

    python_score, python_stable = _without_score(python_result)
    rust_score, rust_stable = _without_score(rust_result)
    assert rust_code == python_code == 0
    assert rust_stable == python_stable
    assert rust_score == pytest.approx(python_score, rel=1e-12, abs=1e-12)
    assert rust_result["ok"] is True
    assert rust_result["claim_eligible"] is False
    assert rust_result["difficulty"]["observed"] is False


def test_native_benchmark_validate_config_preserves_rejection_exit_code(
    tmp_path: Path,
) -> None:
    config = tmp_path / "invalid-config.json"
    value = write_config(config, "20X")
    value["controls"]["same_prompt"] = False
    config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    python_code, python_result = _run(
        "python", "benchmark", "validate-config", f"--config={config}"
    )
    rust_code, rust_result = _run(
        "rust", "benchmark", "validate-config", f"--config={config}"
    )

    python_score, python_stable = _without_score(python_result)
    rust_score, rust_stable = _without_score(rust_result)
    assert rust_code == python_code == 3
    assert rust_stable == python_stable
    assert rust_score == pytest.approx(python_score, rel=1e-12, abs=1e-12)
    assert rust_result["ok"] is False
    assert rust_result["difficulty"]["qualified"] is False
    assert "integrity-failed:same_prompt" in rust_result["difficulty"]["integrity_errors"]


def _tree(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_native_benchmark_generate_repo_matches_python_tree_and_hash(
    tmp_path: Path,
) -> None:
    python_output = tmp_path / "python-repo"
    rust_output = tmp_path / "rust-repo"
    python_output.mkdir()
    rust_output.mkdir()
    (python_output / "stale.txt").write_text("remove me", encoding="utf-8")
    (rust_output / "stale.txt").write_text("remove me", encoding="utf-8")

    common = (
        "benchmark",
        "generate-repo",
        "--files",
        "7",
        "--depth=4",
        "--fanout",
        "3",
        "--faults=2",
    )
    python_code, python_result = _run(
        "python", *common, "--output", str(python_output)
    )
    rust_code, rust_result = _run("rust", *common, "--output", str(rust_output))

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result == {
        "files": 10,
        "depth": 4,
        "fanout": 3,
        "faults": 2,
        "ground_truth_hash": rust_result["ground_truth_hash"],
        "observed_axes": {
            "R": 7.0,
            "C": 12.0,
            "O": 1.0,
            "T": 1.0,
            "P": 1.0,
            "V": 2.0,
            "X": 21.0,
            "H": 1.0,
            "S": 1.0,
            "F": 2.0,
        },
    }
    assert len(rust_result["ground_truth_hash"]) == 64
    assert _tree(rust_output) == _tree(python_output)
    assert "stale.txt" not in _tree(rust_output)
    assert set(_tree(rust_output)) == {
        "fault_0000.py",
        "fault_0001.py",
        "ground_truth.json",
        *(f"module_{index:04d}.py" for index in range(7)),
    }

    if os.name != "nt":
        mode = stat.S_IMODE((rust_output / "ground_truth.json").stat().st_mode)
        assert mode == 0o644


def test_native_benchmark_generate_empty_repo_matches_python(tmp_path: Path) -> None:
    python_output = tmp_path / "python-empty"
    rust_output = tmp_path / "rust-empty"
    arguments = (
        "benchmark",
        "generate-repo",
        "--files=0",
        "--depth=0",
        "--fanout=0",
        "--faults=0",
    )

    python_code, python_result = _run(
        "python", *arguments, "--output", str(python_output)
    )
    rust_code, rust_result = _run(
        "rust", *arguments, "--output", str(rust_output)
    )

    assert rust_code == python_code == 0
    assert rust_result == python_result
    assert rust_result["files"] == 1
    assert rust_result["faults"] == 0
    assert _tree(rust_output) == _tree(python_output)
    assert set(_tree(rust_output)) == {"ground_truth.json"}
