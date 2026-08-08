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


def _run(engine: str, project: Path, *arguments: str) -> tuple[int, Any, str]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    home = project / "home"
    home.mkdir(exist_ok=True)
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
            str(project / "state"),
            "uninstall",
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


def _normalized(value: Any, project: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalized(item, project) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item, project) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<PROJECT>")
    return value


def _write_manifest(project: Path) -> None:
    target_file = project / "managed" / "config.json"
    target_directory = project / "managed" / "skill"
    backup_file = project / ".syntavra" / "install" / "backups" / "file" / "config.json"
    backup_directory = project / ".syntavra" / "install" / "backups" / "directory" / "skill"

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("managed-file", encoding="utf-8")
    target_directory.mkdir(parents=True, exist_ok=True)
    (target_directory / "SKILL.md").write_text("managed-skill", encoding="utf-8")

    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_text("original-file", encoding="utf-8")
    backup_directory.mkdir(parents=True, exist_ok=True)
    (backup_directory / "SKILL.md").write_text("original-skill", encoding="utf-8")

    manifest = {
        "schema_version": 2,
        "version": "0.0.1",
        "release_channel": "pre-release",
        "version_locked": True,
        "project": str(project),
        "scope": "project",
        "changes": [
            {
                "host": "codex",
                "path": str(target_file),
                "action": "merge-config",
                "backup": str(backup_file),
                "mode": "NATIVE_ENFORCED",
            },
            {
                "host": "codex",
                "path": str(target_directory),
                "action": "copy-skill",
                "backup": str(backup_directory),
                "mode": "NATIVE_ENFORCED",
            },
        ],
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    manifest_path = project / ".syntavra" / "install" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_native_uninstall_absent_manifest_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_code, python_value, python_stderr = _run("python", project)
    rust_code, rust_value, rust_stderr = _run("rust", project)
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    assert rust_value == python_value == {
        "ok": True,
        "changes": [],
        "reason": "not-installed",
    }


def test_native_uninstall_dry_run_matches_without_mutation(tmp_path: Path) -> None:
    projects = {
        "python": tmp_path / "python-project",
        "rust": tmp_path / "rust-project",
    }
    results: dict[str, tuple[int, Any, str]] = {}
    for engine, project in projects.items():
        project.mkdir()
        _write_manifest(project)
        results[engine] = _run(engine, project, "--dry-run")
        assert (project / ".syntavra" / "install" / "manifest.json").is_file()
        assert (project / "managed" / "config.json").read_text(encoding="utf-8") == "managed-file"
        assert (project / "managed" / "skill" / "SKILL.md").read_text(encoding="utf-8") == "managed-skill"

    python_code, python_value, python_stderr = results["python"]
    rust_code, rust_value, rust_stderr = results["rust"]
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    assert _normalized(rust_value, projects["rust"]) == _normalized(
        python_value, projects["python"]
    )
    assert rust_value["dry_run"] is True
    assert [Path(row["path"]).name for row in rust_value["changes"]] == [
        "skill",
        "config.json",
    ]


def test_native_uninstall_apply_restores_backups_and_removes_manifest(tmp_path: Path) -> None:
    projects = {
        "python": tmp_path / "python-project",
        "rust": tmp_path / "rust-project",
    }
    results: dict[str, tuple[int, Any, str]] = {}
    for engine, project in projects.items():
        project.mkdir()
        _write_manifest(project)
        results[engine] = _run(engine, project)
        assert not (project / ".syntavra" / "install" / "manifest.json").exists()
        assert (project / "managed" / "config.json").read_text(encoding="utf-8") == "original-file"
        assert (project / "managed" / "skill" / "SKILL.md").read_text(encoding="utf-8") == "original-skill"

    python_code, python_value, python_stderr = results["python"]
    rust_code, rust_value, rust_stderr = results["rust"]
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    assert _normalized(rust_value, projects["rust"]) == _normalized(
        python_value, projects["python"]
    )
    assert rust_value["dry_run"] is False
