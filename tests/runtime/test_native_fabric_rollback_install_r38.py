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
    _tree,
)
from tests.runtime.test_native_install_r38 import _selector_binary


def _rollback(
    engine: str,
    project: Path,
    skill_root: Path,
    transaction_id: str,
    *arguments: str,
    home: Path,
) -> subprocess.CompletedProcess[str]:
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
            "rollback-install",
            transaction_id,
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


def _seed_existing(project: Path) -> dict[str, bytes]:
    config = project / ".codex" / "mcp.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"userSetting": True, "mcpServers": {"other": {"command": "other"}}}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    skill = project / ".codex" / "skills" / "syntavra"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# Original skill\n", encoding="utf-8")
    (skill / "LOCAL.md").write_text("local-only\n", encoding="utf-8")
    return _tree(project / ".codex")


def test_native_fabric_rollback_restores_existing_targets_and_ledger(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    expected_python = _seed_existing(python_project)
    expected_rust = _seed_existing(rust_project)
    assert expected_python == expected_rust

    python_install = _install("python", python_project, skill_root, "codex", home=python_home)
    rust_install = _install("rust", rust_project, skill_root, "codex", home=rust_home)
    assert python_install.returncode == rust_install.returncode == 0
    python_id = _json_stdout(python_install)["transaction_id"]
    rust_id = _json_stdout(rust_install)["transaction_id"]

    python = _rollback(
        "python", python_project, skill_root, python_id, home=python_home
    )
    rust = _rollback("rust", rust_project, skill_root, rust_id, home=rust_home)
    assert python.returncode == rust.returncode == 0
    assert python.stderr == rust.stderr == ""
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
        label="rollback-existing",
    )
    assert rust_value["status"] == "rolled-back"
    assert [row["action"] for row in rust_value["changes"]] == ["restored", "restored"]
    assert _tree(python_project / ".codex") == expected_python
    assert _tree(rust_project / ".codex") == expected_rust
    assert _database_snapshot(
        rust_project, home=rust_home, skill_root=skill_root
    ) == _database_snapshot(
        python_project, home=python_home, skill_root=skill_root
    )


def test_native_fabric_rollback_removes_created_targets_and_is_idempotent(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"

    python_install = _install("python", python_project, skill_root, "codex", home=python_home)
    rust_install = _install("rust", rust_project, skill_root, "codex", home=rust_home)
    python_id = _json_stdout(python_install)["transaction_id"]
    rust_id = _json_stdout(rust_install)["transaction_id"]

    first_python = _rollback(
        "python", python_project, skill_root, python_id, home=python_home
    )
    first_rust = _rollback("rust", rust_project, skill_root, rust_id, home=rust_home)
    assert first_python.returncode == first_rust.returncode == 0
    assert [row["action"] for row in _json_stdout(first_rust)["changes"]] == [
        "removed",
        "removed",
    ]
    assert not (python_project / ".codex" / "mcp.json").exists()
    assert not (rust_project / ".codex" / "mcp.json").exists()

    second_python = _rollback(
        "python", python_project, skill_root, python_id, home=python_home
    )
    second_rust = _rollback("rust", rust_project, skill_root, rust_id, home=rust_home)
    assert second_python.returncode == second_rust.returncode == 0
    _assert_values_equal(
        _json_stdout(second_rust),
        _json_stdout(second_python),
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="rollback-idempotent",
    )


def test_native_fabric_rollback_output_receipt_matches_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = project / "home"
        installed = _install(engine, project, skill_root, "codex", home=home)
        transaction_id = _json_stdout(installed)["transaction_id"]
        output = tmp_path / f"{engine}-rollback.json"
        completed = _rollback(
            engine,
            project,
            skill_root,
            transaction_id,
            "--output",
            str(output),
            home=home,
        )
        assert completed.returncode == 0 and completed.stderr == ""
        receipt = _json_stdout(completed)
        assert receipt == {"ok": True, "output": str(output), "bytes": output.stat().st_size}
        assert json.loads(output.read_text(encoding="utf-8"))["status"] == "rolled-back"


def test_native_fabric_rollback_unknown_transaction_fails_without_rows(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = project / "home"
        completed = _rollback(
            engine,
            project,
            skill_root,
            "host-0-missing000000",
            home=home,
        )
        assert completed.returncode != 0
        database = project / "state" / "host-installations.sqlite3"
        assert database.is_file()
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_install_transactions"
            ).fetchone()[0] == 0
