from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _first_difference, _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    *arguments: str,
    host: str = "codex",
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
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
            host,
            "fabric",
            "platform-plan",
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


def _normalize(value: Any, project: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(item, project) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, project) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<PROJECT>")
    return value


def _assert_equal(
    rust: Any,
    python: Any,
    *,
    rust_project: Path,
    python_project: Path,
) -> None:
    rust_value = _normalize(rust, rust_project)
    python_value = _normalize(python, python_project)
    difference = _first_difference(rust_value, python_value)
    assert difference is None, difference


def _snapshot(project: Path) -> dict[str, Any]:
    database = project / "state" / "competitive-fabric.sqlite3"
    assert database.is_file(), database
    with sqlite3.connect(database) as connection:
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
        events = connection.execute("SELECT COUNT(*) FROM fabric_events").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "tables": tables,
        "indexes": indexes,
        "metadata": metadata,
        "events": events,
        "integrity": integrity,
    }


def _pair(tmp_path: Path, *arguments: str, host: str = "codex") -> tuple[Any, Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, *arguments, host=host)
    rust = _run("rust", rust_project, *arguments, host=host)
    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    _assert_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
    )
    assert _snapshot(rust_project) == _snapshot(python_project)
    return rust_value, python_project, rust_project


def test_native_fabric_platform_plan_defaults_to_global_host(tmp_path: Path) -> None:
    value, _, _ = _pair(tmp_path, host="codex")
    assert value["host"] == "codex"
    assert value["scope"] == "project"
    assert value["mode"] == "MCP_CONTROLLED"
    assert value["files"] == [
        {
            "path": ".codex/mcp.json",
            "merge": {
                "mcpServers": {
                    "syntavra": {"command": "syntavra", "args": ["mcp"]}
                }
            },
        },
        {
            "path": ".codex/skills/syntavra/SKILL.md",
            "source": "bundled syntavra skill",
        },
    ]


def test_native_fabric_platform_plan_claude_hooks_match_python(tmp_path: Path) -> None:
    value, _, _ = _pair(
        tmp_path,
        "--host-name",
        "claude-code",
        "--scope",
        "user",
    )
    assert value["scope"] == "user"
    merge = value["files"][0]["merge"]
    assert merge["statusLine"] == {
        "type": "command",
        "command": "syntavra run statusline",
    }
    assert sorted(merge["hooks"]) == [
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "UserPromptSubmit",
    ]
    assert value["mode"] == "HOOK_ENFORCED"
    assert value["enforced"] is True


def test_native_fabric_platform_plan_generic_mcp_is_explicitly_supported(
    tmp_path: Path,
) -> None:
    value, _, _ = _pair(tmp_path, "--host-name", "generic-mcp")
    assert value["host"] == "generic-mcp"
    assert value["files"] == []
    assert value["mode"] == "MCP_CONTROLLED"
    assert value["capabilities"]["supports_mcp"] is True


def test_native_fabric_platform_plan_all_is_sorted_and_excludes_generic(
    tmp_path: Path,
) -> None:
    value, _, _ = _pair(tmp_path, "--all")
    hosts = [plan["host"] for plan in value["hosts"]]
    assert value["host_count"] == 43
    assert len(hosts) == 43
    assert hosts == sorted(hosts)
    assert "generic-mcp" not in hosts
    assert value["enforced_count"] == sum(
        bool(plan["enforced"]) for plan in value["hosts"]
    )
    assert value["verified_count"] == sum(
        bool(plan["verified_adapter"]) for plan in value["hosts"]
    )


def test_native_fabric_platform_plan_repeated_host_name_uses_last_value(
    tmp_path: Path,
) -> None:
    value, _, _ = _pair(
        tmp_path,
        "--host-name",
        "cursor",
        "--host-name",
        "codex",
    )
    assert value["host"] == "codex"


def test_native_fabric_platform_plan_case_sensitive_unknown_fails_after_ledger_init(
    tmp_path: Path,
) -> None:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python = _run("python", python_project, "--host-name", "Codex")
    rust = _run("rust", rust_project, "--host-name", "Codex")
    assert python.returncode != 0
    assert rust.returncode != 0
    assert _snapshot(rust_project) == _snapshot(python_project)


def test_native_fabric_platform_plan_output_receipt_matches_payload(tmp_path: Path) -> None:
    output = tmp_path / "platform-plan.json"
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"

    python = _run("python", python_project, "--host-name", "codex", "--output", str(output))
    assert python.returncode == 0 and python.stderr == ""
    python_receipt = json.loads(python.stdout)
    python_payload = output.read_bytes()
    python_value = json.loads(python_payload)

    rust = _run("rust", rust_project, "--host-name", "codex", "--output", str(output))
    assert rust.returncode == 0 and rust.stderr == ""
    rust_receipt = json.loads(rust.stdout)
    rust_payload = output.read_bytes()
    rust_value = json.loads(rust_payload)
    assert python_receipt["ok"] is rust_receipt["ok"] is True
    assert python_receipt["output"] == rust_receipt["output"] == str(output)
    assert python_receipt["bytes"] == len(python_payload)
    assert rust_receipt["bytes"] == len(rust_payload)
    _assert_equal(
        rust_value,
        python_value,
        rust_project=rust_project,
        python_project=python_project,
    )
    assert _snapshot(rust_project) == _snapshot(python_project)
