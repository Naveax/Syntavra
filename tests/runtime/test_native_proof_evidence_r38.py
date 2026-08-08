from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKLOADS = ("coding-agent", "repository-task", "tool-routing")


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
        timeout=300,
    )


def _assert_exact(arguments: tuple[str, ...], expected_code: int) -> dict[str, object]:
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == expected_code, (
        python_result.stdout,
        python_result.stderr,
        rust_result.stdout,
        rust_result.stderr,
    )
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    return rust_value


def _row(*, index: int, arm: str) -> dict[str, object]:
    candidate = arm == "syntavra"
    return {
        "receipt_id": f"receipt-{index}-{arm}",
        "provider": "provider",
        "model": "model",
        "request_id": f"request-{index}-{arm}",
        "session_id": f"session-{index}",
        "repository_hash": f"repository-{index % 5}",
        "integration_id": "codex",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "wall_time_ms": 50.0 if candidate else 100.0,
        "input_tokens": 500 if candidate else 1000,
        "cached_input_tokens": 0,
        "output_tokens": 100 if candidate else 200,
        "cost_usd": 1.0 if candidate else 2.0,
        "quality_score": 0.8,
        "success": True,
        "synthetic": False,
        "raw_usage_hash": "a" * 64,
        "workload": WORKLOADS[index % len(WORKLOADS)],
        "arm": arm,
        "task_id": f"task-{index % 10}",
        "repetition": index + 1,
        "metadata": {},
    }


def _verified_rows() -> list[dict[str, object]]:
    return [
        _row(index=index, arm=arm)
        for index in range(30)
        for arm in ("baseline", "syntavra")
    ]


def test_native_receipts_empty_input_fails_closed_exactly(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"receipts": []}), encoding="utf-8")
    value = _assert_exact(("prove", "receipts", str(path)), 4)
    assert value["ok"] is False
    assert value["total"] == 0


def test_native_receipts_valid_live_input_matches_python_exactly(tmp_path: Path) -> None:
    path = tmp_path / "valid.json"
    path.write_text(json.dumps({"receipts": [_row(index=0, arm="syntavra")]}), encoding="utf-8")
    value = _assert_exact(("prove", "receipts", str(path)), 0)
    assert value["ok"] is True
    assert value["live"] == 1
    assert value["valid"] == 1


def test_native_measured_benchmark_matches_python_exactly(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps({"receipts": _verified_rows()}), encoding="utf-8")
    value = _assert_exact(("prove", "benchmark", str(path)), 0)
    assert value["ok"] is True
    assert value["claim"] == "MEASURED_AGENT_BENCHMARK_VERIFIED"
    assert value["metrics"]["pairs"] == 30
    assert value["metrics"]["repositories"] == 5
    assert value["metrics"]["tasks"] == 10
    assert value["metrics"]["workloads"] == 3
    assert value["metrics"]["mean_token_ratio"] == 0.5


def test_native_readiness_matches_python_exactly(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    for name in ("product.json", "mcp-profile.json", "platform-adapters.json"):
        (state / name).write_text("{}\n", encoding="utf-8")
    receipts = tmp_path / "benchmark.json"
    receipts.write_text(json.dumps({"receipts": _verified_rows()}), encoding="utf-8")
    value = _assert_exact(
        (
            "--state-root",
            str(state),
            "prove",
            "readiness",
            "--receipts",
            str(receipts),
        ),
        0,
    )
    assert value["ok"] is True
    assert value["claim"] == "DAILY_CODING_AGENT_READY"
    assert value["checks"]["setup_bundle"] is True
    assert value["checks"]["measured_agent_benchmark"] is True
