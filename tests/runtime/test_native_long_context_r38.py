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


def _assert_exact(arguments: tuple[str, ...], expected_code: int) -> dict[str, object]:
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == expected_code, (
        python_result.stderr,
        rust_result.stderr,
        python_result.stdout,
        rust_result.stdout,
    )
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    return rust_value


def _receipt(*, index: int, arm: str) -> dict[str, object]:
    families = (
        "needle-retrieval",
        "temporal-supersession",
        "multi-hop-evidence",
        "repository-history",
        "cross-session-continuity",
        "recursive-map-reduce",
    )
    tiers = (32_000, 128_000, 1_000_000)
    return {
        "receipt_id": f"receipt-{index}-{arm}",
        "case_id": f"case-{index % 10}",
        "task_family": families[index % len(families)],
        "tier_tokens": tiers[index % len(tiers)],
        "arm": arm,
        "repetition": index + 1,
        "repository_hash": "repository-hash",
        "provider": "provider",
        "model": "model",
        "answer_quality": 0.75,
        "required_fact_recall": 1.0,
        "stale_fact_rejection": 1.0,
        "evidence_precision": 1.0,
        "exact_recovery": arm == "syntavra",
        "forced_restart": False,
        "continuity_restored": arm == "syntavra",
        "wall_time_ms": 50.0 if arm == "syntavra" else 100.0,
        "input_tokens": 400 if arm == "syntavra" else 800,
        "output_tokens": 100 if arm == "syntavra" else 200,
        "synthetic": False,
    }


def test_native_long_context_manifest_matches_python_exactly() -> None:
    value = _assert_exact(("prove", "long-context"), 0)
    assert value["tiers"] == [
        32_000,
        64_000,
        128_000,
        256_000,
        512_000,
        1_000_000,
        2_000_000,
        10_000_000,
    ]


def test_native_long_context_empty_receipts_fail_closed_exactly(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"receipts": []}), encoding="utf-8")
    value = _assert_exact(("prove", "long-context", str(path)), 4)
    assert value["ok"] is False
    assert "no-receipts" in value["reasons"]


def test_native_long_context_verified_fixture_matches_python_exactly(tmp_path: Path) -> None:
    receipts = [
        _receipt(index=index, arm=arm)
        for index in range(30)
        for arm in ("baseline", "syntavra")
    ]
    path = tmp_path / "verified.json"
    path.write_text(json.dumps({"receipts": receipts}), encoding="utf-8")
    value = _assert_exact(("prove", "long-context", str(path)), 0)
    assert value["ok"] is True
    assert value["claim"] == "LONG_CONTEXT_QUALITY_VERIFIED"
    assert value["metrics"]["pairs"] == 30
