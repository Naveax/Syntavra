from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _first_difference, _selector_binary

ROOT = Path(__file__).resolve().parents[2]
_TRANSACTION = re.compile(r"host-[0-9]+-[0-9a-f]{12}")


def _skill(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "# Syntavra\n\nCanonical native fabric install fixture.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "REFERENCE.md").write_text(
        "# Reference\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def _run(
    engine: str,
    project: Path,
    skill_root: Path,
    host: str,
    *arguments: str,
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    selected_home = home or project / "home"
    selected_home.mkdir(parents=True, exist_ok=True)
    state = project / "state"
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    environment = os.environ.copy()
    environment["HOME"] = str(selected_home)
    environment["USERPROFILE"] = str(selected_home)
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
            str(state),
            "--host",
            "codex",
            "fabric",
            "install",
            host,
            "--skill-root",
            str(skill_root),
            "--home",
            str(selected_home),
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


def _normalize(
    value: Any,
    *,
    project: Path,
    home: Path,
    skill_root: Path,
) -> Any:
    state = project / "state"

    def visit(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {name: visit(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [visit(child, key) for child in item]
        if key in {"created_at", "updated_at"} and isinstance(item, (int, float)):
            return f"<{key.upper()}>"
        if key == "transaction_id" and isinstance(item, str):
            return "<TRANSACTION_ID>"
        if isinstance(item, str):
            rendered = item
            for path, marker in (
                (state, "<STATE>"),
                (project, "<PROJECT>"),
                (home, "<HOME>"),
                (skill_root, "<SKILL>"),
            ):
                rendered = rendered.replace(str(path), marker)
            return _TRANSACTION.sub("<TRANSACTION_ID>", rendered)
        return item

    return visit(value)


def _assert_values_equal(
    rust: Any,
    python: Any,
    *,
    rust_project: Path,
    python_project: Path,
    rust_home: Path,
    python_home: Path,
    skill_root: Path,
    label: str,
) -> None:
    rust_value = _normalize(
        rust,
        project=rust_project,
        home=rust_home,
        skill_root=skill_root,
    )
    python_value = _normalize(
        python,
        project=python_project,
        home=python_home,
        skill_root=skill_root,
    )
    difference = _first_difference(rust_value, python_value)
    assert difference is None, {"label": label, "difference": difference}


def _json_stdout(completed: subprocess.CompletedProcess[str]) -> Any:
    assert completed.stdout.strip(), {
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
    }
    return json.loads(completed.stdout)


def _database(project: Path) -> Path:
    return project / "state" / "host-installations.sqlite3"


def _database_snapshot(
    project: Path,
    *,
    home: Path,
    skill_root: Path,
) -> dict[str, Any]:
    database = _database(project)
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        indexes = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%' "
                "ORDER BY name"
            )
        ]
        metadata = dict(connection.execute("SELECT key,value FROM metadata ORDER BY key"))
        rows = []
        for row in connection.execute(
            "SELECT transaction_id,host,scope,root,status,manifest_json,created_at,updated_at "
            "FROM host_install_transactions ORDER BY created_at"
        ):
            value = dict(row)
            value["manifest_json"] = json.loads(value["manifest_json"])
            rows.append(value)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return _normalize(
        {
            "tables": tables,
            "indexes": indexes,
            "metadata": metadata,
            "rows": rows,
            "integrity": integrity,
        },
        project=project,
        home=home,
        skill_root=skill_root,
    )


def _tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _transaction_manifest(project: Path) -> Any:
    manifests = sorted(
        (project / "state" / "host-installations").glob("host-*/manifest.json")
    )
    assert len(manifests) == 1, manifests
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def test_native_fabric_install_codex_dry_run_matches_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"

    python = _run(
        "python",
        python_project,
        skill_root,
        "CoDeX",
        "--dry-run",
        home=python_home,
    )
    rust = _run(
        "rust",
        rust_project,
        skill_root,
        "CoDeX",
        "--dry-run",
        home=rust_home,
    )
    assert rust.returncode == python.returncode == 0
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
        label="codex-dry-run",
    )
    assert rust_value["host"] == "codex"
    assert rust_value["status"] == "dry-run"
    assert [change["path"] for change in rust_value["changes"]] == [
        ".codex/mcp.json",
        ".codex/skills/syntavra",
    ]
    assert not (rust_project / ".codex" / "mcp.json").exists()
    assert not (python_project / ".codex" / "mcp.json").exists()
    assert _database_snapshot(
        rust_project,
        home=rust_home,
        skill_root=skill_root,
    ) == _database_snapshot(
        python_project,
        home=python_home,
        skill_root=skill_root,
    )


def test_native_fabric_install_codex_apply_preserves_config_and_records_state(
    tmp_path: Path,
) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    existing = {
        "userSetting": True,
        "mcpServers": {"other": {"command": "other-tool", "args": ["serve"]}},
    }
    for project in (python_project, rust_project):
        target = project / ".codex" / "mcp.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    python = _run(
        "python",
        python_project,
        skill_root,
        "codex",
        home=python_home,
    )
    rust = _run(
        "rust",
        rust_project,
        skill_root,
        "codex",
        home=rust_home,
    )
    assert rust.returncode == python.returncode == 0
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
        label="codex-apply",
    )
    assert rust_value["status"] == "applied"
    assert rust_value["verification"]["ok"] is True
    assert _tree(rust_project / ".codex") == _tree(python_project / ".codex")
    config = json.loads((rust_project / ".codex" / "mcp.json").read_text(encoding="utf-8"))
    assert config["userSetting"] is True
    assert config["mcpServers"]["other"]["command"] == "other-tool"
    assert config["mcpServers"]["syntavra"] == {
        "command": "syntavra",
        "args": ["mcp"],
    }
    _assert_values_equal(
        _transaction_manifest(rust_project),
        _transaction_manifest(python_project),
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="codex-manifest",
    )
    assert _database_snapshot(
        rust_project,
        home=rust_home,
        skill_root=skill_root,
    ) == _database_snapshot(
        python_project,
        home=python_home,
        skill_root=skill_root,
    )


def test_native_fabric_install_cursor_repeated_apply_keeps_one_managed_block(
    tmp_path: Path,
) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    for project in (python_project, rust_project):
        rules = project / ".cursor" / "rules" / "syntavra.mdc"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text("user preface\n", encoding="utf-8", newline="\n")

    for iteration in range(2):
        python = _run(
            "python",
            python_project,
            skill_root,
            "cursor",
            home=python_home,
        )
        rust = _run(
            "rust",
            rust_project,
            skill_root,
            "cursor",
            home=rust_home,
        )
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
            label=f"cursor-apply-{iteration}",
        )

    assert _tree(rust_project / ".cursor") == _tree(python_project / ".cursor")
    text = (rust_project / ".cursor" / "rules" / "syntavra.mdc").read_text(
        encoding="utf-8"
    )
    assert text.startswith("user preface")
    assert text.count("<!-- SYNTAVRA:BEGIN managed-host-integration -->") == 1
    assert text.count("<!-- SYNTAVRA:END managed-host-integration -->") == 1
    with sqlite3.connect(_database(rust_project)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM host_install_transactions"
        ).fetchone()[0] == 2
    assert _database_snapshot(
        rust_project,
        home=rust_home,
        skill_root=skill_root,
    ) == _database_snapshot(
        python_project,
        home=python_home,
        skill_root=skill_root,
    )


def test_native_fabric_install_user_scope_isolated_from_project(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"

    python = _run(
        "python",
        python_project,
        skill_root,
        "codex",
        "--scope",
        "user",
        home=python_home,
    )
    rust = _run(
        "rust",
        rust_project,
        skill_root,
        "codex",
        "--scope",
        "user",
        home=rust_home,
    )
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
        label="user-scope",
    )
    assert _tree(rust_home / ".codex") == _tree(python_home / ".codex")
    assert not (rust_project / ".codex").exists()
    assert not (python_project / ".codex").exists()


def test_native_fabric_install_invalid_config_fails_without_transaction(
    tmp_path: Path,
) -> None:
    skill_root = _skill(tmp_path / "skill")
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"
    for project in (python_project, rust_project):
        target = project / ".codex" / "mcp.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{invalid-json\n", encoding="utf-8")

    python = _run(
        "python",
        python_project,
        skill_root,
        "codex",
        home=python_home,
    )
    rust = _run(
        "rust",
        rust_project,
        skill_root,
        "codex",
        home=rust_home,
    )
    assert python.returncode != 0
    assert rust.returncode != 0
    assert (python_project / ".codex" / "mcp.json").read_text(encoding="utf-8") == "{invalid-json\n"
    assert (rust_project / ".codex" / "mcp.json").read_text(encoding="utf-8") == "{invalid-json\n"
    for project in (python_project, rust_project):
        storage = project / "state" / "host-installations"
        assert storage.is_dir()
        assert list(storage.iterdir()) == []
        with sqlite3.connect(_database(project)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM host_install_transactions"
            ).fetchone()[0] == 0


def test_native_fabric_install_output_receipt_matches_python(tmp_path: Path) -> None:
    skill_root = _skill(tmp_path / "skill")
    output = tmp_path / "install.json"
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = python_project / "home"
    rust_home = rust_project / "home"

    python = _run(
        "python",
        python_project,
        skill_root,
        "codex",
        "--dry-run",
        "--output",
        str(output),
        home=python_home,
    )
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = _json_stdout(python)
    python_payload = output.read_bytes()
    python_value = json.loads(python_payload)

    rust = _run(
        "rust",
        rust_project,
        skill_root,
        "codex",
        "--dry-run",
        "--output",
        str(output),
        home=rust_home,
    )
    assert rust.returncode == 0 and rust.stderr == ""
    rust_receipt = _json_stdout(rust)
    rust_payload = output.read_bytes()
    rust_value = json.loads(rust_payload)
    assert python_receipt["ok"] is True
    assert rust_receipt["ok"] is True
    assert python_receipt["output"] == rust_receipt["output"] == str(output)
    assert python_receipt["bytes"] == len(python_payload)
    assert rust_receipt["bytes"] == len(rust_payload)
    _assert_values_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
        rust_home=rust_home,
        python_home=python_home,
        skill_root=skill_root,
        label="output-receipt",
    )
