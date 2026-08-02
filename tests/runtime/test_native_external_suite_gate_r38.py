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
    syntavra = arm == "syntavra"
    return {
        "receipt_id": f"receipt-{index}-{arm}",
        "suite_id": "oolong",
        "task_id": f"task-{index}",
        "arm": arm,
        "repetition": index + 1,
        "dataset_version": "oolong-v1",
        "harness_commit": "a" * 40,
        "verifier_commit": "b" * 40,
        "environment_image_digest": "sha256:" + "c" * 64,
        "repository_commit": "",
        "provider": "provider",
        "model": "model",
        "model_config_hash": "d" * 64,
        "result_artifact_hash": "e" * 64,
        "raw_provider_receipt_hash": "f" * 64,
        "quality_score": 0.75,
        "success": True,
        "input_tokens": 500 if syntavra else 1000,
        "cached_input_tokens": 0,
        "output_tokens": 100 if syntavra else 200,
        "cost_usd": 1.0 if syntavra else 2.0,
        "wall_time_ms": 50.0 if syntavra else 100.0,
        "recursive_calls": 0,
        "synthetic": False,
        "metadata": {},
    }


def test_native_external_suite_empty_receipts_fail_closed_exactly(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"receipts": []}), encoding="utf-8")
    value = _assert_exact(("prove", "external-suite", str(path)), 4)
    assert value["ok"] is False
    assert "no-receipts" in value["reasons"]


def test_native_external_suite_verified_pairs_match_python_exactly(tmp_path: Path) -> None:
    rows = [
        _row(index=index, arm=arm)
        for index in range(30)
        for arm in ("baseline", "syntavra")
    ]
    path = tmp_path / "verified.json"
    path.write_text(json.dumps({"receipts": rows}), encoding="utf-8")
    value = _assert_exact(
        ("prove", "external-suite", str(path), "--suite", "oolong"),
        0,
    )
    assert value["ok"] is True
    assert value["claim"] == "EXTERNAL_SUITE_EVIDENCE_VERIFIED"
    assert value["metrics"]["pairs"] == 30
    assert value["metrics"]["mean_token_ratio"] == 0.5
    assert value["metrics"]["mean_cost_ratio"] == 0.5
    assert value["metrics"]["mean_wall_time_ratio"] == 0.5
