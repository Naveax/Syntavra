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


def _compare(command: str, path: Path, expected_code: int) -> dict[str, object]:
    arguments = (command, "gate", str(path))
    python_result = _run("python", *arguments)
    rust_result = _run("rust", *arguments)
    assert python_result.returncode == rust_result.returncode == expected_code
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    return rust_value


def test_native_signalbench_gate_fails_closed_exactly(tmp_path: Path) -> None:
    rows = [
        {
            "task_id": "one",
            "repetition": 1,
            "arm_id": "syntavra",
            "success": True,
            "active_tokens": 10,
            "wall_seconds": 1,
            "security_regressions": 0,
            "pair_key": "one",
            "synthetic": False,
            "source_kind": "live-external-arm",
        },
        {
            "task_id": "one",
            "repetition": 1,
            "arm_id": "plain-baseline",
            "success": True,
            "active_tokens": 100,
            "wall_seconds": 10,
            "security_regressions": 0,
            "pair_key": "one",
            "synthetic": False,
            "source_kind": "live-external-arm",
        },
    ]
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    value = _compare("signalbench", path, 4)
    assert value["claim"] == "EXTERNAL_SUPERIORITY_NOT_PROVEN"


def test_native_signalbench2_gate_preserves_infinity_json(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    value = _compare("signalbench2", path, 4)
    assert value["metrics"]["token_ratio"] == float("inf")
    assert value["metrics"]["wall_ratio"] == float("inf")


def test_native_signalbench_gate_proves_complete_fixture(tmp_path: Path) -> None:
    rows: list[dict[str, object]] = []
    for repetition in range(1, 31):
        for task_index in range(1, 151):
            pair_key = f"pair-{task_index:03d}-{repetition:02d}"
            common = {
                "task_id": f"task-{task_index:03d}",
                "repetition": repetition,
                "success": True,
                "security_regressions": 0,
                "pair_key": pair_key,
                "synthetic": False,
                "source_kind": "live-external-arm",
            }
            rows.append(
                {
                    **common,
                    "arm_id": "syntavra",
                    "active_tokens": 10,
                    "wall_seconds": 1,
                }
            )
            rows.append(
                {
                    **common,
                    "arm_id": "plain-baseline",
                    "active_tokens": 100,
                    "wall_seconds": 10,
                }
            )
    path = tmp_path / "complete.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    value = _compare("signalbench", path, 0)
    assert value["ok"] is True
    assert value["claim"] == "SUPERIORITY_PROVEN"
