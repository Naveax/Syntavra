from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_TRANSACTION = re.compile(r"host-[0-9]+-[0-9a-f]{12}")


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
        timeout=900,
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


def _run(engine: str, project: Path, *arguments: str) -> tuple[int, Any, str]:
    state = project / "state"
    home = project / "home"
    home.mkdir(parents=True, exist_ok=True)
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
            str(state),
            "install",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _normalized(value: Any, project: Path) -> Any:
    state = project / "state"

    def visit(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {name: visit(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [visit(child, key) for child in item]
        if key in {"wall_time_ms", "started_at", "completed_at", "created_at", "installed_at", "updated_at"}:
            return f"<{key.upper()}>"
        if key == "transaction_id" and isinstance(item, str):
            return "<TRANSACTION_ID>"
        if isinstance(item, str):
            text = item.replace(str(state), "<STATE>").replace(str(project), "<PROJECT>")
            return _TRANSACTION.sub("<TRANSACTION_ID>", text)
        return item

    return visit(value)


def _assert_pair(
    python_project: Path,
    rust_project: Path,
    *arguments: str,
) -> tuple[Any, Any]:
    python_result = _run("python", python_project, *arguments)
    rust_result = _run("rust", rust_project, *arguments)
    python_code, python_value, python_stderr = python_result
    rust_code, rust_value, rust_stderr = rust_result
    assert python_code == rust_code == 0, {
        "python": python_result,
        "rust": rust_result,
    }
    assert python_stderr == rust_stderr == ""
    assert _normalized(rust_value, rust_project) == _normalized(
        python_value, python_project
    )
    return python_value, rust_value


def _tree_digest(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_state_json_matches(python_project: Path, rust_project: Path, name: str) -> None:
    python_value = json.loads((python_project / "state" / name).read_text(encoding="utf-8"))
    rust_value = json.loads((rust_project / "state" / name).read_text(encoding="utf-8"))
    assert _normalized(rust_value, rust_project) == _normalized(
        python_value, python_project
    )


def test_native_install_empty_dry_run_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    _, value = _assert_pair(python_project, rust_project)
    assert value["ok"] is True
    assert value["dry_run"] is True
    assert value["profile"] == "minimal"
    assert value["plan"]["detected_hosts"] == []
    assert value["plan"]["installable_hosts"] == []
    assert value["plan"]["estimated_seconds"] == 17.0
    assert value["setup_bundle"] is None


def test_native_install_codex_dry_run_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    for project in (python_project, rust_project):
        project.mkdir()
        (project / ".codex").mkdir()
    _, value = _assert_pair(
        python_project,
        rust_project,
        "--dry-run",
        "--mcp-profile",
        "minimal",
    )
    assert value["plan"]["detected_hosts"] == ["codex"]
    assert value["plan"]["installable_hosts"] == ["codex"]
    assert value["host_results"][0]["status"] == "dry-run"
    assert [row["path"] for row in value["host_results"][0]["changes"]] == [
        ".codex/mcp.json",
        ".codex/skills/syntavra",
    ]
    assert not (rust_project / ".codex" / "mcp.json").exists()


def test_native_install_empty_apply_matches_product_bundle(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    _, value = _assert_pair(
        python_project,
        rust_project,
        "--apply",
        "--mcp-profile=balanced",
    )
    assert value["ok"] is True
    assert value["dry_run"] is False
    assert value["profile"] == "balanced"
    assert value["host_results"] == []
    for name in (
        "config.json",
        "product.json",
        "mcp-profile.json",
        "platform-adapters.json",
        "install-receipt.json",
    ):
        _assert_state_json_matches(python_project, rust_project, name)
    config = json.loads((rust_project / "state" / "config.json").read_text(encoding="utf-8"))
    assert config["mcp_profile"] == "balanced"
    assert config["hosts"] == []


def test_native_install_codex_apply_matches_host_transaction(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    for project in (python_project, rust_project):
        project.mkdir()
        (project / ".codex").mkdir()
    _, value = _assert_pair(python_project, rust_project, "--apply")
    assert value["host_results"][0]["verification"]["ok"] is True
    assert value["host_results"][0]["status"] == "applied"

    python_config = json.loads(
        (python_project / ".codex" / "mcp.json").read_text(encoding="utf-8")
    )
    rust_config = json.loads(
        (rust_project / ".codex" / "mcp.json").read_text(encoding="utf-8")
    )
    assert rust_config == python_config
    assert rust_config["mcpServers"]["syntavra"] == {
        "command": "syntavra",
        "args": ["mcp"],
    }
    assert _tree_digest(rust_project / ".codex" / "skills" / "syntavra") == _tree_digest(
        python_project / ".codex" / "skills" / "syntavra"
    )

    for name in (
        "config.json",
        "product.json",
        "mcp-profile.json",
        "platform-adapters.json",
        "install-receipt.json",
    ):
        _assert_state_json_matches(python_project, rust_project, name)

    database = sqlite3.connect(rust_project / "state" / "host-installations.sqlite3")
    try:
        row = database.execute(
            "SELECT host,scope,status FROM host_install_transactions"
        ).fetchone()
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        database.close()
    assert row == ("codex", "project", "applied")
