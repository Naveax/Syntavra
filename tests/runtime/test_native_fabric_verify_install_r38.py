from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.runtime.test_native_fabric_install_r38 import (
    ROOT,
    _assert_values_equal,
    _database_snapshot,
    _json_stdout,
    _run as _install,
    _skill,
)
from tests.runtime.test_native_install_r38 import _selector_binary


def _verify(
    engine: str,
    project: Path,
    skill_root: Path,
    host: str,
    *arguments: str,
    home: Path,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
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
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(project / "state"),
            "--host",
            "codex",
            "fabric",
            "verify-install",
            host,
            "--skill-root",
            str(skill_root),
            "--home",
            str(home),
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


def _assert_pair(
    tmp_path: Path,
    skill_root: Path,
    *arguments: str,
    host: str = "codex",
) -> tuple[dict, dict, Path, Path, Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    python = _verify(
        "python", python_project, skill_root, host, *arguments, home=python_home
    )
    rust = _verify("rust", rust_project, skill_root, host, *arguments, home=rust_home)
    assert rust.returncode == python.returncode
    assert rust.stderr == python.stderr == ""
    python_value = _json_stdout(python)
    rust_value = _json_stdout(rust)
    _assert_values_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="verify-install",
    )
    return rust_value, python_value, rust_project, python_project, rust_home, python_home


def test_native_fabric_verify_missing_install_matches_python_and_exit_code(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    value, _, rust_project, python_project, rust_home, python_home = _assert_pair(
        tmp_path, skill_root
    )
    assert value == {
        "ok": False,
        "host": "codex",
        "scope": "project",
        "root": str(rust_project),
        "mode": "RUNTIME_PARTIAL",
        "reasons": ["missing-config", "missing-skill"],
        "details": {
            "config": {"path": ".codex/mcp.json", "hash": ""},
            "skill": {"path": ".codex/skills/syntavra", "hash": ""},
        },
    }
    assert _database_snapshot(
        rust_project, home=rust_home, skill_root=skill_root
    ) == _database_snapshot(
        python_project, home=python_home, skill_root=skill_root
    )


def test_native_fabric_verify_after_install_matches_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    assert _install(
        "python", python_project, skill_root, "codex", home=python_home
    ).returncode == 0
    assert _install(
        "rust", rust_project, skill_root, "codex", home=rust_home
    ).returncode == 0

    python = _verify(
        "python", python_project, skill_root, "codex", home=python_home
    )
    rust = _verify("rust", rust_project, skill_root, "codex", home=rust_home)
    assert rust.returncode == python.returncode == 0
    assert rust.stderr == python.stderr == ""
    _assert_values_equal(
        _json_stdout(rust),
        _json_stdout(python),
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="verify-installed",
    )


def test_native_fabric_verify_invalid_config_reason_order_matches_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    for project in (python_project, rust_project):
        config = project / ".codex" / "mcp.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("{invalid\n", encoding="utf-8")
    python = _verify(
        "python", python_project, skill_root, "codex", home=python_home
    )
    rust = _verify("rust", rust_project, skill_root, "codex", home=rust_home)
    assert rust.returncode == python.returncode == 3
    python_value = _json_stdout(python)
    rust_value = _json_stdout(rust)
    _assert_values_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="verify-invalid-config",
    )
    assert rust_value["reasons"] == ["invalid-config-json", "missing-skill"]


def test_native_fabric_verify_user_scope_and_output_receipt_match_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    receipts = {}
    values = {}
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = project / "home"
        output = tmp_path / f"{engine}-verify.json"
        completed = _verify(
            engine,
            project,
            skill_root,
            "codex",
            "--scope",
            "user",
            "--output",
            str(output),
            home=home,
        )
        assert completed.returncode == 3
        assert completed.stderr == ""
        receipts[engine] = _json_stdout(completed)
        values[engine] = json.loads(output.read_text(encoding="utf-8"))
        assert receipts[engine] == {
            "ok": True,
            "output": str(output),
            "bytes": output.stat().st_size,
        }
        database = project / "state" / "host-installations.sqlite3"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_install_transactions"
            ).fetchone()[0] == 0
    assert values["rust"]["reasons"] == values["python"]["reasons"]
    assert values["rust"]["scope"] == values["python"]["scope"] == "user"
