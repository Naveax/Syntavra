from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ID = "github-copilot-vscode"
CONFIG_PATH = ".vscode/mcp.json"


def _run(
    engine: str,
    project: Path,
    home: Path,
    desired: dict[str, Any],
    *,
    apply: bool,
    path: str = CONFIG_PATH,
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
    arguments = [
        *prefix,
        "--engine",
        engine,
        "--project",
        str(project),
        "--state-root",
        str(project / "state"),
        "run",
        "adapter-configure",
        ADAPTER_ID,
        path,
        json.dumps(desired, ensure_ascii=False, sort_keys=True),
    ]
    if apply:
        arguments.append("--apply")
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _normalize(value: Any, *, project: Path, home: Path) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "receipt_id":
                normalized[key] = "<receipt-id>"
            elif key == "created_at":
                normalized[key] = "<created-at>"
            else:
                normalized[key] = _normalize(item, project=project, home=home)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, project=project, home=home) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<project>").replace(str(home), "<home>")
    return value


def _receipt_values(project: Path, home: Path) -> list[dict[str, Any]]:
    root = project / "state" / "unified" / "adapter-receipts"
    values = [
        _normalize(json.loads(path.read_text(encoding="utf-8")), project=project, home=home)
        for path in root.glob("*.json")
    ]
    return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))


def _state_snapshot(project: Path, home: Path) -> dict[str, Any]:
    root = project / "state"
    files: list[str] = []
    databases: dict[str, Any] = {}
    payloads: dict[str, Any] = {}
    if not root.exists():
        return {"files": files, "databases": databases, "payloads": payloads, "receipts": []}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(("-wal", "-shm")):
            continue
        relative = path.relative_to(root).as_posix()
        if "/adapter-receipts/" in f"/{relative}":
            continue
        files.append(relative)
        if path.suffix in {".sqlite", ".sqlite3", ".db"}:
            with sqlite3.connect(path) as connection:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                ]
                counts: dict[str, int] = {}
                for table in tables:
                    escaped = table.replace('"', '""')
                    counts[table] = connection.execute(
                        f'SELECT COUNT(*) FROM "{escaped}"'
                    ).fetchone()[0]
                databases[relative] = {"tables": tables, "counts": counts}
        elif "key" not in path.name.casefold():
            payloads[relative] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {
        "files": files,
        "databases": databases,
        "payloads": payloads,
        "receipts": _receipt_values(project, home),
    }


def _pair(
    tmp_path: Path,
    desired: dict[str, Any],
    *,
    apply: bool,
    current: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    for project in (python_project, rust_project):
        project.mkdir(parents=True)
        if current is not None:
            target = project / CONFIG_PATH
            target.parent.mkdir(parents=True)
            target.write_text(current, encoding="utf-8", newline="\n")
    python = _run("python", python_project, python_home, desired, apply=apply)
    rust = _run("rust", rust_project, rust_home, desired, apply=apply)
    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = _normalize(
        json.loads(python.stdout), project=python_project, home=python_home
    )
    rust_value = _normalize(json.loads(rust.stdout), project=rust_project, home=rust_home)
    return rust_value, python_value, rust_project, python_project, rust_home, python_home


def test_native_adapter_configure_dry_run_matches_python(tmp_path: Path) -> None:
    desired = {"servers": {"syntavra": {"args": ["run", "platform-status"], "command": "syntavra"}}}
    rust, python, rust_project, python_project, rust_home, python_home = _pair(
        tmp_path, desired, apply=False
    )
    assert rust == python
    assert rust["maturity"] == "Contract"
    assert rust["checks"] == {
        **rust["checks"],
        "changed": True,
        "applied": False,
        "declared_path": True,
        "valid_json": True,
    }
    assert not (rust_project / CONFIG_PATH).exists()
    assert not (python_project / CONFIG_PATH).exists()
    assert _state_snapshot(rust_project, rust_home) == _state_snapshot(
        python_project, python_home
    )


def test_native_adapter_configure_apply_matches_python(tmp_path: Path) -> None:
    desired = {
        "servers": {
            "syntavra": {
                "args": ["run", "platform-status"],
                "command": "syntavra",
            }
        }
    }
    rust, python, rust_project, python_project, rust_home, python_home = _pair(
        tmp_path, desired, apply=True
    )
    assert rust == python
    assert rust["maturity"] == "Configured"
    assert rust["detected"] is True
    assert rust["checks"]["applied"] is True
    assert (rust_project / CONFIG_PATH).read_bytes() == (
        python_project / CONFIG_PATH
    ).read_bytes()
    assert json.loads((rust_project / CONFIG_PATH).read_text(encoding="utf-8")) == desired
    assert _state_snapshot(rust_project, rust_home) == _state_snapshot(
        python_project, python_home
    )


def test_native_adapter_configure_merge_and_backup_match_python(tmp_path: Path) -> None:
    current = '{\n  "servers": {\n    "existing": {\n      "command": "existing"\n    }\n  },\n  "version": 1\n}\n'
    desired = {
        "servers": {
            "syntavra": {
                "args": ["run", "platform-status"],
                "command": "syntavra",
            }
        }
    }
    rust, python, rust_project, python_project, rust_home, python_home = _pair(
        tmp_path, desired, apply=True, current=current
    )
    assert rust == python
    assert rust["rollback"]["backup"].endswith(".bak")
    assert (rust_project / CONFIG_PATH).read_bytes() == (
        python_project / CONFIG_PATH
    ).read_bytes()
    assert _state_snapshot(rust_project, rust_home) == _state_snapshot(
        python_project, python_home
    )


def test_native_adapter_configure_repeated_apply_is_idempotent(tmp_path: Path) -> None:
    desired = {"servers": {"syntavra": {"command": "syntavra"}}}
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    for engine, project, home in (
        ("python", python_project, python_home),
        ("rust", rust_project, rust_home),
    ):
        first = _run(engine, project, home, desired, apply=True)
        assert first.returncode == 0, (first.stdout, first.stderr)
    python_second = _run("python", python_project, python_home, desired, apply=True)
    rust_second = _run("rust", rust_project, rust_home, desired, apply=True)
    assert rust_second.returncode == python_second.returncode == 0
    rust = _normalize(json.loads(rust_second.stdout), project=rust_project, home=rust_home)
    python = _normalize(
        json.loads(python_second.stdout), project=python_project, home=python_home
    )
    assert rust == python
    assert rust["checks"]["changed"] is False
    assert rust["checks"]["applied"] is False
    assert rust["changed_paths"] == []
    assert rust["rollback"] == {}
    assert _state_snapshot(rust_project, rust_home) == _state_snapshot(
        python_project, python_home
    )


def test_native_adapter_configure_rejects_undeclared_path(tmp_path: Path) -> None:
    desired = {"servers": {}}
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    python = _run(
        "python", python_project, python_home, desired, apply=True, path="outside.json"
    )
    rust = _run("rust", rust_project, rust_home, desired, apply=True, path="outside.json")
    assert rust.returncode != 0
    assert python.returncode != 0
    assert not (rust_project / "outside.json").exists()
    assert not (python_project / "outside.json").exists()
    assert _state_snapshot(rust_project, rust_home) == _state_snapshot(
        python_project, python_home
    )
