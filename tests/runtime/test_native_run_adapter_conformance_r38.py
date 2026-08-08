from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]
HASH_FIELDS = (
    "adapter_id",
    "maturity",
    "operation",
    "ok",
    "detected",
    "changed_paths",
    "capabilities",
    "checks",
    "created_at",
)


def _run(
    engine: str,
    project: Path,
    adapter_id: str,
    *,
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
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(project / "state"),
            "run",
            "adapter-conformance",
            adapter_id,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _receipt_files(project: Path) -> list[Path]:
    directory = project / "state" / "unified" / "adapter-receipts"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _assert_hash(receipt: dict[str, Any]) -> None:
    body = {key: receipt[key] for key in HASH_FIELDS}
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert receipt["receipt_id"] == expected
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00",
        receipt["created_at"],
    )


def _normalize(
    value: Any,
    *,
    project: Path,
    home: Path,
    bin_path: Path | None,
) -> Any:
    if isinstance(value, dict):
        output = {
            key: _normalize(item, project=project, home=home, bin_path=bin_path)
            for key, item in value.items()
        }
        if "created_at" in output:
            output["created_at"] = "<created-at>"
        if "receipt_id" in output:
            output["receipt_id"] = "<receipt-id>"
        return output
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


def _state_shape(project: Path) -> dict[str, Any]:
    root = project / "state"
    files: list[str] = []
    sqlite: dict[str, Any] = {}
    for path in sorted(root.rglob("*")) if root.exists() else []:
        if not path.is_file() or path.name.endswith(("-wal", "-shm")):
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("unified/adapter-receipts/"):
            files.append("unified/adapter-receipts/<receipt>.json")
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
                counts = {}
                for table in tables:
                    escaped = table.replace('"', '""')
                    counts[table] = connection.execute(
                        f'SELECT COUNT(*) FROM "{escaped}"'
                    ).fetchone()[0]
                sqlite[relative] = {"tables": tables, "counts": counts}
    return {"files": files, "sqlite": sqlite}


def _pair(
    tmp_path: Path,
    *,
    adapter_id: str = "codex-cli",
    detected: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path, Path, Path | None, Path | None]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    python_bin = tmp_path / "python-bin" if detected else None
    rust_bin = tmp_path / "rust-bin" if detected else None
    if detected:
        assert python_bin is not None and rust_bin is not None
        for project, bin_path in (
            (python_project, python_bin),
            (rust_project, rust_bin),
        ):
            bin_path.mkdir(parents=True, exist_ok=True)
            executable = bin_path / "codex"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            (project / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
            (project / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    python = _run(
        "python",
        python_project,
        adapter_id,
        home=python_home,
        path=python_bin,
    )
    rust = _run(
        "rust",
        rust_project,
        adapter_id,
        home=rust_home,
        path=rust_bin,
    )
    assert rust.returncode == python.returncode == 0, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    _assert_hash(python_value)
    _assert_hash(rust_value)
    assert _normalize(
        rust_value,
        project=rust_project,
        home=rust_home,
        bin_path=rust_bin,
    ) == _normalize(
        python_value,
        project=python_project,
        home=python_home,
        bin_path=python_bin,
    )
    return (
        rust_value,
        python_value,
        rust_project,
        python_project,
        rust_home,
        python_home,
        rust_bin,
        python_bin,
    )


def test_native_adapter_conformance_contract_receipt_matches_python(tmp_path: Path) -> None:
    rust, python, rust_project, python_project, *_ = _pair(tmp_path)
    assert rust["maturity"] == python["maturity"] == "Contract"
    assert rust["detected"] is python["detected"] is False
    assert rust["operation"] == python["operation"] == "conformance"
    assert rust["claim_boundary"] == python["claim_boundary"]
    for project, value in ((rust_project, rust), (python_project, python)):
        receipts = _receipt_files(project)
        assert len(receipts) == 1
        assert receipts[0].stem == value["receipt_id"].split(":", 1)[1]
        assert json.loads(receipts[0].read_text(encoding="utf-8")) == value
        assert (project / "state" / "unified" / "adapter-backups").is_dir()
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_conformance_detected_maturity_matches_python(tmp_path: Path) -> None:
    rust, python, rust_project, python_project, *_ = _pair(tmp_path, detected=True)
    assert rust["maturity"] == python["maturity"] == "Configured"
    assert rust["detected"] is python["detected"] is True
    detection = rust["checks"]["detection"]
    assert detection["commands"] == ["codex"]
    assert detection["paths"] == [str(rust_project / "AGENTS.md")]
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_conformance_unknown_adapter_fails_without_receipt(tmp_path: Path) -> None:
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = tmp_path / f"{engine}-home"
        completed = _run(engine, project, "missing-adapter", home=home)
        assert completed.returncode != 0
        assert _receipt_files(project) == []
        assert (project / "state" / "unified" / "adapter-receipts").is_dir()
        assert (project / "state" / "unified" / "adapter-backups").is_dir()
