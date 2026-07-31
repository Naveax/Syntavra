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


def test_dual_engine_inventory_is_complete_and_fail_closed() -> None:
    result = MODULE.verify()
    assert result["ok"] is True
    assert result["claim"] == "DUAL_ENGINE_PARITY_INCOMPLETE"
    assert result["full"] is False
    assert result["python"]["public_command_count"] == 257
    assert result["rust"]["native_public_command_count"] == 13
    assert result["rust"]["missing_native_public_command_count"] == 244
    assert result["policy"]["hidden_fallback_forbidden"] is True
    assert result["policy"]["one_install_contains_python_and_rust"] is True


def test_full_claim_cannot_open_while_native_commands_are_missing() -> None:
    with pytest.raises(RuntimeError, match="full dual-engine parity not reached"):
        MODULE.verify(require_full=True)


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
