from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    selected = os.environ.get("SYNTAVRA_R38_SELECTOR")
    if selected:
        path = Path(selected)
        assert path.is_file(), path
        return path
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bin", "syntavra"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    path = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert path.is_file(), path
    return path


def _run(
    engine: str,
    project: Path,
    state_root: Path,
    home: Path,
    *arguments: str,
) -> tuple[int, Any, str]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = ""
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state_root),
            *arguments,
        ],
        cwd=project,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=300,
        env=environment,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _assert_doctor_matches(project: Path, tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    python_code, python_value, python_stderr = _run(
        "python", project, tmp_path / "python-state", home, "doctor"
    )
    rust_code, rust_value, rust_stderr = _run(
        "rust", project, tmp_path / "rust-state", home, "doctor"
    )
    assert rust_code == python_code
    assert rust_stderr == python_stderr == ""
    assert rust_value == python_value
    return rust_value


def test_native_operator_doctor_matches_empty_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    value = _assert_doctor_matches(project, tmp_path)
    assert value["ok"] is True
    assert value["installed"] is False
    assert value["runtime"]["state"] == "PRE_RELEASE_READY"
    assert value["warnings"] == [
        {"code": "not-installed", "repair": "syntavra setup --apply"}
    ]


def test_native_operator_doctor_matches_invalid_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for state_name in ("python-state", "rust-state"):
        state = tmp_path / state_name
        state.mkdir()
        (state / "config.json").write_text("not-json", encoding="utf-8")
    value = _assert_doctor_matches(project, tmp_path)
    assert value["installed"] is False
    assert value["warnings"][0]["code"] == "not-installed"


def test_native_operator_doctor_matches_incomplete_product_bundle(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = {
        "version": "0.0.1",
        "channel": "pre-release",
        "project_root": str(project),
        "hosts": [],
        "host_transactions": [],
        "mcp_profile": "minimal",
        "product_commands": ["setup", "status", "run", "prove"],
        "installed_at": 1.0,
    }
    for state_name in ("python-state", "rust-state"):
        state = tmp_path / state_name
        state.mkdir()
        (state / "config.json").write_text(
            json.dumps(config, sort_keys=True), encoding="utf-8"
        )
    value = _assert_doctor_matches(project, tmp_path)
    assert value["installed"] is True
    assert value["runtime"]["state"] == "PRE_RELEASE_INSTALLED"
    assert value["warnings"] == [
        {
            "code": "product-bundle-incomplete",
            "repair": "syntavra repair --apply",
        }
    ]
