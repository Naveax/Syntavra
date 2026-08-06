from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "verify_dual_engine_public_surface.py"
SPEC = importlib.util.spec_from_file_location("verify_dual_engine_public_surface", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


def _write_statusline_state(state_root: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "optimization-mode.json").write_text(
        json.dumps({"mode": "review"}, separators=(",", ":")),
        encoding="utf-8",
    )
    analytics = state_root / "analytics"
    analytics.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "source": "build",
            "original_tokens": 2000,
            "visible_tokens": 500,
            "saved_tokens": 1500,
            "provider_cost_before": 1.25,
            "provider_cost_after": 0.25,
        },
        {
            "source": "cache",
            "original_tokens": 1000,
            "visible_tokens": 800,
            "saved_tokens": 200,
            "provider_cost_before": 0.5,
            "provider_cost_after": 0.4,
        },
    ]
    (analytics / "savings.jsonl").write_text(
        "\n".join(
            [
                *(json.dumps(row, separators=(",", ":")) for row in rows),
                "not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_dual_engine_inventory_is_complete_and_fail_closed() -> None:
    result = MODULE.verify()
    assert result["ok"] is True
    assert result["claim"] == "DUAL_ENGINE_PARITY_INCOMPLETE"
    assert result["full"] is False
    assert result["python"]["public_command_count"] == 245
    assert result["rust"]["native_public_command_count"] == 153
    assert result["rust"]["missing_native_public_command_count"] == 92
    assert result["policy"]["hidden_fallback_forbidden"] is True
    assert result["policy"]["one_install_contains_python_and_rust"] is True


def test_full_claim_cannot_open_while_native_commands_are_missing() -> None:
    with pytest.raises(RuntimeError, match="full dual-engine parity not reached"):
        MODULE.verify(require_full=True)


def test_native_version_matches_python_exactly() -> None:
    assert _rust_engine("version") == _python_engine("version")


def test_native_context_stress_matches_python_exactly() -> None:
    assert _rust_engine("context-stress") == _python_engine("context-stress")


def test_native_context_stress_custom_window_matches_python_exactly() -> None:
    arguments = (
        "context-stress",
        "--budget",
        "1024",
        "--max-tier",
        "256000",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_context_stress_empty_tiers_matches_exit_code() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    arguments = ("context-stress", "--max-tier", "-1")
    python_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    rust_result = subprocess.run(
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
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert python_result.returncode == 3, python_result.stderr
    assert rust_result.returncode == 3, rust_result.stderr
    assert json.loads(rust_result.stdout) == json.loads(python_result.stdout)


def test_native_migration_plan_matches_python_exactly(tmp_path: Path) -> None:
    arguments = (
        "--project",
        str(tmp_path),
        "migrate",
        "plan",
        "state/missing.sqlite3",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_pipeline_description_matches_python_exactly() -> None:
    assert _rust_engine("pipeline", "describe") == _python_engine("pipeline", "describe")


def test_native_plugin_inventory_matches_python_exactly() -> None:
    assert _rust_engine("plugins", "list") == _python_engine("plugins", "list")


def test_native_empty_scheduler_stats_matches_python_exactly(tmp_path: Path) -> None:
    arguments = ("--state-root", str(tmp_path / "state"), "scheduler", "stats")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_empty_scheduler_list_matches_python_exactly(tmp_path: Path) -> None:
    arguments = (
        "--state-root",
        str(tmp_path / "state"),
        "scheduler",
        "list",
        "--state",
        "queued",
        "--limit",
        "7",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_empty_telemetry_metrics_matches_python_exactly() -> None:
    assert _rust_engine("telemetry", "metrics") == _python_engine("telemetry", "metrics")


def test_native_proof_status_matches_python_exactly() -> None:
    arguments = ("proof", "status")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_prove_plan_matches_python_exactly() -> None:
    arguments = ("prove", "plan")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_cache_amortization_matches_python_exactly() -> None:
    arguments = (
        "run",
        "cache-amortize",
        "--write",
        "100",
        "--read",
        "0",
        "--uncached",
        "1000",
        "--requests",
        "1",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_empty_cache_health_matches_python_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    arguments = ("--state-root", str(state_root), "run", "cache-health")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_empty_statusline_matches_python_exactly(tmp_path: Path) -> None:
    arguments = ("--state-root", str(tmp_path / "state"), "run", "statusline")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_populated_statusline_matches_python_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_statusline_state(state_root)
    arguments = ("--state-root", str(state_root), "run", "statusline")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_verbose_statusline_matches_python_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    _write_statusline_state(state_root)
    arguments = (
        "--state-root",
        str(state_root),
        "run",
        "statusline",
        "--verbose",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_provider_route_matches_python_exactly(tmp_path: Path) -> None:
    candidates = [
        {
            "provider": "openai",
            "model": "gpt-x",
            "quality": 0.9,
            "quota_remaining": 0.8,
            "latency_ms": 100.0,
            "subscription": True,
            "priority": 4,
            "max_complexity": "reasoning",
            "context_window": 200000,
        },
        {
            "provider": "local",
            "model": "qwen",
            "quality": 0.7,
            "quota_remaining": 1.0,
            "latency_ms": 30.0,
            "priority": 1,
            "max_complexity": "complex",
            "context_window": 64000,
        },
    ]
    candidate_path = tmp_path / "provider-candidates.json"
    candidate_path.write_text(
        json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    arguments = (
        "run",
        "provider-route",
        "architecture migration proof",
        str(candidate_path),
        "--changed-files",
        "9",
        "--tokens",
        "20000",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_delegate_matches_python_exactly() -> None:
    arguments = (
        "run",
        "delegate",
        "Implement the API layer. Verify coverage and benchmark results. Audit security permissions. Update database migration and memory index.",
        "--context-path",
        "src/lib.rs",
        "--context-path",
        "tests/runtime",
        "--max-tasks",
        "4",
    )
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_delegate_small_task_matches_python_exactly() -> None:
    arguments = ("run", "delegate", "Rename the variable carefully.")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_read_route_matches_python_exactly() -> None:
    arguments = ("run", "route", "syntavra.output.search")
    assert _rust_engine(*arguments) == _python_engine(*arguments)


def test_native_denied_route_matches_python_output_and_exit_code() -> None:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    arguments = ("run", "route", "host.shell", "--profile", "audit")
    python_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    rust_result = subprocess.run(
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
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert python_result.returncode == 5, python_result.stderr
    assert rust_result.returncode == 5, rust_result.stderr
    assert json.loads(rust_result.stdout) == json.loads(python_result.stdout)
