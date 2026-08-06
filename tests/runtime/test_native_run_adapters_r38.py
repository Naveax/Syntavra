from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    *,
    detect: bool = False,
    home: Path,
    path: Path | None = None,
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
    environment["PATH"] = str(path) if path is not None else ""
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
        "--host",
        "codex",
        "run",
        "adapters",
    ]
    if detect:
        arguments.append("--detect")
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


def _normalize(value: Any, *, project: Path, home: Path, bin_path: Path | None) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(item, project=project, home=home, bin_path=bin_path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _normalize(item, project=project, home=home, bin_path=bin_path)
            for item in value
        ]
    if isinstance(value, str):
        rendered = value.replace(str(project), "<project>").replace(str(home), "<home>")
        if bin_path is not None:
            rendered = rendered.replace(str(bin_path), "<bin>")
        return rendered
    return value


def _state_snapshot(project: Path) -> dict[str, Any]:
    root = project / "state"
    if not root.exists():
        return {"files": [], "sqlite": {}}
    files: list[str] = []
    databases: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(("-wal", "-shm")):
            continue
        relative = path.relative_to(root).as_posix()
        files.append(relative)
        if path.suffix in {".sqlite", ".sqlite3", ".db"}:
            with sqlite3.connect(path) as connection:
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                ]
                counts = {}
                for table in tables:
                    escaped = table.replace('"', '""')
                    counts[table] = connection.execute(
                        f'SELECT COUNT(*) FROM "{escaped}"'
                    ).fetchone()[0]
                databases[relative] = {"tables": tables, "counts": counts}
        elif path.stat().st_size <= 4096 and "key" not in path.name.casefold():
            databases[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
    return {"files": files, "sqlite": databases}


def _pair(
    tmp_path: Path,
    *,
    detect: bool = False,
    configure: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    python_bin = tmp_path / "python-bin"
    rust_bin = tmp_path / "rust-bin"
    if configure:
        for project, home, bin_path in (
            (python_project, python_home, python_bin),
            (rust_project, rust_home, rust_bin),
        ):
            bin_path.mkdir(parents=True, exist_ok=True)
            executable = bin_path / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            (project / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
            (project / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
            config = home / ".claude" / "settings.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("{}\n", encoding="utf-8")
    python = _run(
        "python",
        python_project,
        detect=detect,
        home=python_home,
        path=python_bin if configure else None,
    )
    rust = _run(
        "rust",
        rust_project,
        detect=detect,
        home=rust_home,
        path=rust_bin if configure else None,
    )
    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = _normalize(
        json.loads(python.stdout),
        project=python_project,
        home=python_home,
        bin_path=python_bin if configure else None,
    )
    rust_value = _normalize(
        json.loads(rust.stdout),
        project=rust_project,
        home=rust_home,
        bin_path=rust_bin if configure else None,
    )
    return rust_value, python_value, rust_project, python_project, rust_home, python_home


def test_native_run_adapters_catalog_and_validation_match_python(tmp_path: Path) -> None:
    rust, python, rust_project, python_project, _, _ = _pair(tmp_path)
    assert rust == python
    assert rust["ok"] is True
    assert rust["validation"]["adapters"] == 20
    assert rust["validation"]["inventory_gate"] is True
    assert len(rust["adapters"]) == 20
    assert _state_snapshot(rust_project) == _state_snapshot(python_project)


def test_native_run_adapters_detection_matches_python(tmp_path: Path) -> None:
    rust, python, _, _, _, _ = _pair(tmp_path, detect=True, configure=True)
    assert rust == python
    by_id = {row["adapter_id"]: row for row in rust["adapters"]}
    assert by_id["codex-cli"]["detected"] is True
    assert by_id["codex-cli"]["detected_commands"] == ["codex"]
    assert by_id["codex-cli"]["existing_configs"] == ["<project>/AGENTS.md"]
    assert by_id["claude-code"]["existing_configs"] == [
        "<home>/.claude/settings.json"
    ]


def test_native_run_adapters_detect_false_has_no_detection_fields(tmp_path: Path) -> None:
    rust, python, _, _, _, _ = _pair(tmp_path)
    assert rust == python
    assert all("detected" not in row for row in rust["adapters"])
