from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.verifier_graph import VerifierGraph

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


def _run(engine: str, state_root: Path, *arguments: str) -> dict[str, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--state-root",
            str(state_root),
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, {
        "engine": engine,
        "arguments": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


def _prepare_state(state_root: Path) -> dict[str, str]:
    graph = VerifierGraph(state_root / "verifier.sqlite3")
    first = graph.record(
        ("cargo", "test", "--workspace"),
        tree_hash="tree-a",
        environment_hash="env-a",
        dependency_hash="deps-a",
        toolchain_hash="toolchain-a",
        success=True,
        exit_code=0,
        evidence_handle="sc://sha256/" + "a" * 64,
        affected_paths=("src/lib.rs", "Cargo.toml"),
    )
    second = graph.record(
        ("python", "-m", "pytest", "-q"),
        tree_hash="tree-b",
        environment_hash="env-b",
        dependency_hash="deps-b",
        toolchain_hash="toolchain-b",
        success=False,
        exit_code=1,
        evidence_handle="sc://sha256/" + "b" * 64,
        affected_paths=("src/lib.rs", "tests/test_cli.py"),
    )
    database = sqlite3.connect(state_root / "verifier.sqlite3")
    try:
        database.execute(
            "UPDATE verifier_results SET created_at=100.0 WHERE cache_key=?",
            (first.cache_key,),
        )
        database.execute(
            "UPDATE verifier_results SET created_at=200.0 WHERE cache_key=?",
            (second.cache_key,),
        )
        database.commit()
    finally:
        database.close()
    return {"first": first.cache_key, "second": second.cache_key}


def _state_pair(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    source = tmp_path / "source"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    keys = _prepare_state(source)
    shutil.copytree(source, python_state)
    shutil.copytree(source, rust_state)
    return python_state, rust_state, keys


def test_native_verifier_lookup_hit_matches_python(tmp_path: Path) -> None:
    python_state, rust_state, keys = _state_pair(tmp_path)
    arguments = (
        "verifier",
        "lookup",
        "cargo",
        "test",
        "--workspace",
        "--tree-hash",
        "tree-a",
        "--environment-hash",
        "env-a",
        "--dependency-hash",
        "deps-a",
        "--toolchain-hash",
        "toolchain-a",
    )

    python_result = _run("python", python_state, *arguments)
    rust_result = _run("rust", rust_state, *arguments)

    assert rust_result == python_result
    assert rust_result == {
        "cache_key": keys["first"],
        "command": ["cargo", "test", "--workspace"],
        "tree_hash": "tree-a",
        "environment_hash": "env-a",
        "dependency_hash": "deps-a",
        "toolchain_hash": "toolchain-a",
        "success": True,
        "exit_code": 0,
        "evidence_handle": "sc://sha256/" + "a" * 64,
        "affected_paths": ["Cargo.toml", "src/lib.rs"],
        "created_at": 100.0,
    }


def test_native_verifier_lookup_miss_matches_python(tmp_path: Path) -> None:
    python_state, rust_state, _ = _state_pair(tmp_path)
    arguments = (
        "verifier",
        "lookup",
        "cargo",
        "check",
        "--tree-hash=missing-tree",
        "--environment-hash=missing-env",
        "--dependency-hash=missing-deps",
        "--toolchain-hash=missing-toolchain",
    )
    assert _run("rust", rust_state, *arguments) == _run(
        "python", python_state, *arguments
    ) == {"hit": False}


def test_native_verifier_invalidation_matches_python_order(tmp_path: Path) -> None:
    python_state, rust_state, keys = _state_pair(tmp_path)
    arguments = (
        "verifier",
        "invalidated-by",
        "--path",
        "src/lib.rs",
        "--path",
        "Cargo.toml",
        "--path=unrelated.txt",
    )

    python_result = _run("python", python_state, *arguments)
    rust_result = _run("rust", rust_state, *arguments)

    assert rust_result == python_result
    assert rust_result == {
        "invalidated": [
            {
                "cache_key": keys["second"],
                "overlap": ["src/lib.rs"],
                "command": ["python", "-m", "pytest", "-q"],
            },
            {
                "cache_key": keys["first"],
                "overlap": ["Cargo.toml", "src/lib.rs"],
                "command": ["cargo", "test", "--workspace"],
            },
        ]
    }


def test_native_verifier_empty_invalidation_matches_python(tmp_path: Path) -> None:
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    arguments = ("verifier", "invalidated-by", "--path", "src/lib.rs")
    assert _run("rust", rust_state, *arguments) == _run(
        "python", python_state, *arguments
    ) == {"invalidated": []}
