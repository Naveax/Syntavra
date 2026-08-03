from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.util import stable_project_id

ROOT = Path(__file__).resolve().parents[2]


def _command(argv: list[str], *, success: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if success:
        assert completed.returncode == 0, {
            "argv": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    else:
        assert completed.returncode != 0, {
            "argv": argv,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    return completed


def _json_command(argv: list[str]) -> Any:
    return json.loads(_command(argv).stdout)


@lru_cache(maxsize=1)
def _selector_binary() -> Path:
    if shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bins"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    suffix = ".exe" if sys.platform == "win32" else ""
    selector = ROOT / "target" / "debug" / f"syntavra{suffix}"
    assert selector.is_file(), selector
    return selector


def _python_argv(project: Path, state_root: Path, handle: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "syntavra_runtime.engine_entry",
        "--engine",
        "python",
        "--project",
        str(project),
        "--state-root",
        str(state_root),
        "evidence",
        "describe",
        handle,
    ]


def _rust_argv(project: Path, state_root: Path, handle: str) -> list[str]:
    return [
        str(_selector_binary()),
        "--engine",
        "rust",
        "--project",
        str(project),
        "--state-root",
        str(state_root),
        "evidence",
        "describe",
        handle,
    ]


def _write_metadata(
    state_root: Path,
    *,
    digest: str,
    project_id: str,
    schema_version: int | str = 3,
) -> dict[str, Any]:
    metadata = {
        "schema_version": schema_version,
        "digest": digest,
        "bytes": 4096,
        "stored_bytes": 4140,
        "project_id": project_id,
        "kind": "test-fixture",
        "created_at": 1_775_171_200.0,
        "expires_at": None,
        "encryption": {
            "algorithm": "AES-256-GCM",
            "key_version": 4,
            "mode": "encrypted",
        },
        "provenance": [
            {
                "source": "r38-differential",
                "repository_commit": "commit-r38",
                "nested": {"stable": True},
            }
        ],
    }
    directory = state_root / "evidence" / "metadata"
    directory.mkdir(parents=True)
    (directory / f"{digest}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return metadata


def test_native_evidence_description_matches_python_exactly(tmp_path: Path) -> None:
    project = tmp_path / "project"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    digest = "a" * 64
    handle = f"sc://sha256/{digest}"
    project_id = stable_project_id(project)
    expected = _write_metadata(
        python_state,
        digest=digest,
        project_id=project_id,
    )
    _write_metadata(rust_state, digest=digest, project_id=project_id)

    python_result = _json_command(_python_argv(project, python_state, handle))
    rust_result = _json_command(_rust_argv(project, rust_state, handle))

    assert rust_result == python_result == expected
    assert rust_result["encryption"]["mode"] == "encrypted"
    assert rust_result["provenance"][0]["nested"]["stable"] is True


def test_native_evidence_description_accepts_numeric_schema_string(tmp_path: Path) -> None:
    project = tmp_path / "project"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    digest = "b" * 64
    handle = f"sc://sha256/{digest}"
    project_id = stable_project_id(project)
    expected = _write_metadata(
        python_state,
        digest=digest,
        project_id=project_id,
        schema_version="3",
    )
    _write_metadata(
        rust_state,
        digest=digest,
        project_id=project_id,
        schema_version="3",
    )

    assert _json_command(_python_argv(project, python_state, handle)) == expected
    assert _json_command(_rust_argv(project, rust_state, handle)) == expected


def test_native_evidence_description_rejects_cross_project_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    digest = "c" * 64
    handle = f"sc://sha256/{digest}"
    for engine, state_root, argv in (
        ("python", tmp_path / "python-state", _python_argv),
        ("rust", tmp_path / "rust-state", _rust_argv),
    ):
        _write_metadata(
            state_root,
            digest=digest,
            project_id="0" * 64,
        )
        completed = _command(argv(project, state_root, handle), success=False)
        combined = completed.stdout + completed.stderr
        assert "scope" in combined.lower(), {"engine": engine, "output": combined}


def test_native_evidence_description_rejects_invalid_handle(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    invalid = "sc://sha256/ABC"
    _command(_python_argv(project, tmp_path / "python-state", invalid), success=False)
    _command(_rust_argv(project, tmp_path / "rust-state", invalid), success=False)
