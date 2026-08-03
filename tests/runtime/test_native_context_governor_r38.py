from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(engine: str, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if engine == "rust" and shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    argv = (
        ["cargo", "run", "--quiet", "--locked", "--bin", "syntavra", "--", "--engine", "rust"]
        if engine == "rust"
        else [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python"]
    )
    return subprocess.run(
        [*argv, "--project", str(project), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _assert_exact(project: Path, *arguments: str) -> dict[str, object]:
    python_result = _run("python", project, *arguments)
    rust_result = _run("rust", project, *arguments)
    assert python_result.returncode == rust_result.returncode == 0, (
        python_result.stdout,
        python_result.stderr,
        rust_result.stdout,
        rust_result.stderr,
    )
    assert rust_result.stderr == python_result.stderr == ""
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    return rust_value


def test_native_context_default_evaluate_matches_python(tmp_path: Path) -> None:
    value = _assert_exact(
        tmp_path,
        "context",
        "--used",
        "900",
        "--window",
        "1000",
        "--churn",
        "0.25",
        "--evidence-pressure",
        "0.5",
    )
    assert value["level"] == 6
    assert value["mandatory_split"] is True


def test_native_context_explicit_evaluate_matches_python(tmp_path: Path) -> None:
    value = _assert_exact(
        tmp_path,
        "context",
        "evaluate",
        "--used",
        "510",
        "--window",
        "1000",
    )
    assert value["actions"] == ["evict_duplicates", "drop_raw_success_logs"]


def _items() -> dict[str, object]:
    return {
        "items": [
            {
                "item_id": "policy",
                "role": "policy",
                "text": "Policy",
                "tokens": 40,
                "utility": 9.0,
                "confidence": 1.0,
                "mandatory": True,
                "stable": True,
            },
            {
                "item_id": "evidence",
                "role": "evidence",
                "text": "Evidence",
                "tokens": 35,
                "utility": 8.0,
                "confidence": 0.9,
                "dependencies": ["policy"],
            },
            {
                "item_id": "optional-a",
                "role": "detail",
                "text": "A",
                "tokens": 20,
                "utility": 3.0,
                "confidence": 0.8,
            },
            {
                "item_id": "optional-b",
                "role": "detail",
                "text": "B",
                "tokens": 25,
                "utility": 2.0,
                "confidence": 0.7,
            },
        ]
    }


def test_native_context_pack_matches_python(tmp_path: Path) -> None:
    source = tmp_path / "context.json"
    source.write_text(json.dumps(_items()), encoding="utf-8")
    value = _assert_exact(
        tmp_path,
        "context",
        "pack",
        "--input",
        str(source),
        "--budget",
        "95",
        "--mandatory-role",
        "evidence",
    )
    assert value["mandatory_satisfied"] is True
    assert value["selected_ids"] == ["policy", "optional-a", "evidence"]


def test_native_context_pack_over_budget_matches_python(tmp_path: Path) -> None:
    source = tmp_path / "context.json"
    source.write_text(json.dumps(_items()), encoding="utf-8")
    value = _assert_exact(
        tmp_path,
        "context",
        "pack",
        "--input",
        str(source),
        "--budget",
        "30",
        "--mandatory-role",
        "evidence",
    )
    assert value["mandatory_satisfied"] is False
    assert value["reasons"] == ["mandatory-over-budget:75>30"]
