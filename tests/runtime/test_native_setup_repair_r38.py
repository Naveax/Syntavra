from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import (
    _assert_equal,
    _assert_state_json_matches,
    _normalized,
    _selector_binary,
    _tree_digest,
)

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    command: str,
    *arguments: str,
) -> tuple[int, Any, str]:
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
            command,
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
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _assert_pair(
    python_project: Path,
    rust_project: Path,
    command: str,
    *arguments: str,
) -> tuple[Any, Any]:
    python_result = _run("python", python_project, command, *arguments)
    rust_result = _run("rust", rust_project, command, *arguments)
    python_code, python_value, python_stderr = python_result
    rust_code, rust_value, rust_stderr = rust_result
    assert rust_code == python_code, {
        "python": python_result,
        "rust": rust_result,
    }
    assert rust_stderr == python_stderr == ""
    _assert_equal(
        _normalized(rust_value, rust_project),
        _normalized(python_value, python_project),
        f"{command}-output",
    )
    return python_value, rust_value


def test_native_setup_empty_dry_run_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    _, value = _assert_pair(python_project, rust_project, "setup")
    assert value["ok"] is True
    assert value["dry_run"] is True
    assert value["plan"]["installable_hosts"] == []


def test_native_setup_codex_apply_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    for project in (python_project, rust_project):
        project.mkdir()
        (project / ".codex").mkdir()
    _, value = _assert_pair(
        python_project,
        rust_project,
        "setup",
        "--apply",
        "--mcp-profile",
        "minimal",
    )
    assert value["ok"] is True
    assert value["host_results"][0]["verification"]["ok"] is True
    python_config = json.loads(
        (python_project / ".codex" / "mcp.json").read_text(encoding="utf-8")
    )
    rust_config = json.loads(
        (rust_project / ".codex" / "mcp.json").read_text(encoding="utf-8")
    )
    _assert_equal(rust_config, python_config, "setup-codex-config")
    _assert_equal(
        _tree_digest(rust_project / ".codex" / "skills" / "syntavra"),
        _tree_digest(python_project / ".codex" / "skills" / "syntavra"),
        "setup-codex-skill-tree",
    )


def test_native_repair_plan_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    _, value = _assert_pair(python_project, rust_project, "repair")
    assert value == {
        "ok": True,
        "apply": False,
        "actions": ["syntavra setup --apply"],
        "remaining": [
            {
                "code": "not-installed",
                "repair": "syntavra setup --apply",
            }
        ],
    }


def test_native_repair_apply_installs_missing_product(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    _, value = _assert_pair(
        python_project,
        rust_project,
        "repair",
        "--apply",
    )
    assert value == {
        "ok": True,
        "apply": True,
        "actions": ["syntavra setup --apply"],
        "remaining": [],
    }
    for name in (
        "config.json",
        "product.json",
        "mcp-profile.json",
        "platform-adapters.json",
        "install-receipt.json",
    ):
        _assert_state_json_matches(python_project, rust_project, name)


def test_native_repair_restores_only_missing_bundle_files(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    for engine, project in (("python", python_project), ("rust", rust_project)):
        code, value, stderr = _run(engine, project, "install", "--apply")
        assert code == 0, {"engine": engine, "value": value, "stderr": stderr}
        (project / "state" / "product.json").unlink()
        before_receipt = (project / "state" / "install-receipt.json").read_bytes()
        project.joinpath("before-receipt.bin").write_bytes(before_receipt)

    _, value = _assert_pair(
        python_project,
        rust_project,
        "repair",
        "--apply",
    )
    assert value == {
        "ok": True,
        "apply": True,
        "actions": ["syntavra repair --apply"],
        "remaining": [],
    }
    for project in (python_project, rust_project):
        assert (project / "state" / "product.json").is_file()
        assert (project / "state" / "install-receipt.json").read_bytes() == (
            project / "before-receipt.bin"
        ).read_bytes()
    for name in ("product.json", "mcp-profile.json", "platform-adapters.json"):
        _assert_state_json_matches(python_project, rust_project, name)


def test_native_setup_repair_mode_matches_python(tmp_path: Path) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    python_value, rust_value = _assert_pair(
        python_project,
        rust_project,
        "setup",
        "--repair",
    )
    assert python_value == rust_value
    assert rust_value["actions"] == ["syntavra setup --apply"]
