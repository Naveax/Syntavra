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


def _run_pair(arguments: tuple[str, ...], expected_code: int) -> tuple[dict[str, object], dict[str, object]]:
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == expected_code, (
        python_result.stdout,
        python_result.stderr,
        rust_result.stdout,
        rust_result.stderr,
    )
    return json.loads(python_result.stdout), json.loads(rust_result.stdout)


def _assert_exact(arguments: tuple[str, ...], expected_code: int) -> dict[str, object]:
    python_value, rust_value = _run_pair(arguments, expected_code)
    assert rust_value == python_value
    return rust_value


def _maturity_document() -> dict[str, object]:
    observed = "2025-01-01T00:00:00+00:00"
    onboarding = [
        {
            "receipt_id": f"onboarding-{index}",
            "observed_at": observed,
            "user_hash": f"user-{index % 50}",
            "repository_hash": f"repository-{index % 100}",
            "integration_id": f"integration-{index % 5}",
            "operating_system": ("linux", "windows", "macos")[index % 3],
            "install_wall_time_ms": 1_000.0,
            "success": True,
            "rollback_verified": True,
            "doctor_passed": True,
            "synthetic": False,
            "version": "0.0.1",
            "channel": "pre-release",
        }
        for index in range(1_000)
    ]
    distributions = [
        {
            "receipt_id": "distribution-pypi",
            "observed_at": observed,
            "channel_name": "pypi",
            "package_name": "syntavra-runtime",
            "version": "0.0.1",
            "downloads": 500,
            "unique_installations": 125,
            "source_verified": True,
            "synthetic": False,
        },
        {
            "receipt_id": "distribution-github",
            "observed_at": observed,
            "channel_name": "github",
            "package_name": "syntavra-runtime",
            "version": "0.0.1",
            "downloads": 500,
            "unique_installations": 125,
            "source_verified": True,
            "synthetic": False,
        },
    ]
    releases = [
        {
            "receipt_id": f"release-{index}",
            "published_at": published,
            "artifact_id": f"artifact-{index}",
            "version": "0.0.1",
            "channel": "pre-release",
            "signed": True,
            "provenance": True,
            "source_verified": True,
            "synthetic": False,
        }
        for index, published in enumerate(
            (
                "2025-01-01T00:00:00+00:00",
                "2025-01-20T00:00:00+00:00",
                "2025-02-10T00:00:00+00:00",
                "2025-03-01T00:00:00+00:00",
            )
        )
    ]
    return {
        "onboarding": onboarding,
        "distributions": distributions,
        "releases": releases,
    }


def _signal_run(*, task: int, arm: str, quota_cost: float) -> dict[str, object]:
    return {
        "run_id": f"run-{task}-{arm}",
        "task_id": f"task-{task}",
        "arm_id": arm,
        "repetition": 1,
        "success": True,
        "verifier_success": True,
        "verified_work": 1.0,
        "wall_seconds": 1.0,
        "exit_code": 0,
        "fresh_input_tokens": 100,
        "cached_input_tokens": 0,
        "output_tokens": 10,
        "reasoning_tokens": 0,
        "quota_cost": quota_cost,
        "model_turns": 1,
        "tool_calls": 1,
        "wait_calls": 0,
        "compactions": 0,
        "security_regressions": 0,
        "verifier_skips": 0,
        "repository_tree": "tree",
        "prompt_hash": "prompt",
        "verifier_hash": "verifier",
        "permissions_hash": "permissions",
        "cache_mode": "cold",
        "artifact_dir": "artifact",
        "error": "",
        "provider_observed": True,
        "provider": "provider",
        "model": "model",
        "request_id_hash": "request",
        "provider_receipt_hash": "receipt",
    }


def test_native_maturity_empty_document_fails_closed_exactly(tmp_path: Path) -> None:
    path = tmp_path / "empty-maturity.json"
    path.write_text("{}\n", encoding="utf-8")
    value = _assert_exact(("prove", "maturity", str(path)), 4)
    assert value["ok"] is False
    assert value["metrics"]["days"] == 0.0


def test_native_maturity_verified_document_matches_stable_python_fields(tmp_path: Path) -> None:
    path = tmp_path / "maturity.json"
    path.write_text(json.dumps(_maturity_document()), encoding="utf-8")
    python_value, rust_value = _run_pair(("prove", "maturity", str(path)), 0)
    python_days = float(python_value["metrics"].pop("days"))
    rust_days = float(rust_value["metrics"].pop("days"))
    assert abs(python_days - rust_days) < 0.01
    assert rust_value == python_value
    assert rust_value["ok"] is True
    assert rust_value["claim"] == "PUBLIC_PRODUCT_MATURITY_VERIFIED"


def test_native_provider_billed_empty_results_fail_closed_exactly(tmp_path: Path) -> None:
    path = tmp_path / "empty-results.json"
    path.write_text(json.dumps({"results": []}), encoding="utf-8")
    value = _assert_exact(("prove", "provider-billed", str(path)), 4)
    assert value["claimable_superiority"] is False
    assert value["valid_pairs"] == 0


def test_native_provider_billed_verified_pairs_match_python_exactly(tmp_path: Path) -> None:
    rows = [
        _signal_run(task=task, arm=arm, quota_cost=2.0 if arm == "plain-host" else 1.0)
        for task in range(10)
        for arm in ("plain-host", "syntavra-minimal")
    ]
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"results": rows}), encoding="utf-8")
    value = _assert_exact(("prove", "provider-billed", str(path)), 0)
    assert value["claimable_superiority"] is True
    assert value["valid_pairs"] == 10
    assert value["provider_observed_pairs"] == 10
    assert value["median_efficiency_ratio"] == 2.0
    assert value["confidence_interval_95"] == [2.0, 2.0]
