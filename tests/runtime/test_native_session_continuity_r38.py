from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    return json.loads(completed.stdout)


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


def _python_engine(project: Path, state: Path, *arguments: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *arguments,
        ]
    )


def _rust_engine(project: Path, state: Path, *arguments: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *arguments,
        ]
    )


def _normalize_dynamic(value: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(value))
    if "wall_time_ms" in normalized:
        normalized["wall_time_ms"] = "<dynamic>"
    return normalized


def _build_history(project: Path, state: Path, *, events: int = 40) -> None:
    opened = _python_engine(
        project,
        state,
        "run",
        "session-open",
        "--session-id",
        "session-r38",
        "--metadata",
        json.dumps({"owner": "r38", "purpose": "parity"}, sort_keys=True, separators=(",", ":")),
    )
    assert opened["ok"] is True
    for sequence in range(1, events + 1):
        payload = {
            "task": f"task-{sequence}",
            "decision": "continue" if sequence % 2 else "verify",
            "result": sequence,
            "path": f"src/module_{sequence:02}.rs",
        }
        appended = _python_engine(
            project,
            state,
            "run",
            "session-append",
            "session-r38",
            "tool-result" if sequence % 3 else "decision",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        assert appended["ok"] is True
        assert appended["event"]["sequence"] == sequence


def test_native_session_compaction_matches_python_root_and_context(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _build_history(project, source_state)
    shutil.copytree(source_state, python_state)
    shutil.copytree(source_state, rust_state)

    python_result = _python_engine(
        project,
        python_state,
        "run",
        "session-compact",
        "session-r38",
        "--force",
    )
    rust_result = _rust_engine(
        project,
        rust_state,
        "run",
        "session-compact",
        "session-r38",
        "--force",
    )

    assert _normalize_dynamic(rust_result) == _normalize_dynamic(python_result)
    assert rust_result["root_summary_id"] == python_result["root_summary_id"]
    assert rust_result["events"] == 40
    assert rust_result["exact_history_events"] == 40
    assert rust_result["verification"]["ok"] is True


def test_each_engine_accepts_the_other_summary_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    python_compacted = tmp_path / "python-compacted"
    rust_compacted = tmp_path / "rust-compacted"
    project.mkdir()
    _build_history(project, source_state)
    shutil.copytree(source_state, python_compacted)
    shutil.copytree(source_state, rust_compacted)

    python_compaction = _python_engine(
        project,
        python_compacted,
        "run",
        "session-compact",
        "session-r38",
        "--force",
    )
    rust_compaction = _rust_engine(
        project,
        rust_compacted,
        "run",
        "session-compact",
        "session-r38",
        "--force",
    )
    assert rust_compaction["root_summary_id"] == python_compaction["root_summary_id"]

    rust_reads_python = _rust_engine(
        project,
        python_compacted,
        "run",
        "session-continuity",
        "session-r38",
        "--token-budget",
        "32000",
    )
    python_reads_rust = _python_engine(
        project,
        rust_compacted,
        "run",
        "session-continuity",
        "session-r38",
        "--token-budget",
        "32000",
    )

    assert _normalize_dynamic(rust_reads_python) == _normalize_dynamic(python_reads_rust)
    for receipt in (rust_reads_python, python_reads_rust):
        assert receipt["continuity_restored"] is True
        assert receipt["exact_recovery"] is True
        assert receipt["forced_restart"] is False
        assert receipt["events"] == 40
        assert receipt["root_summary_id"] == python_compaction["root_summary_id"]
        assert receipt["claim"] == "SESSION_CONTINUITY_INTERNALLY_VERIFIED"


def test_native_continuity_without_compaction_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    project.mkdir()
    _build_history(project, source_state, events=3)
    shutil.copytree(source_state, python_state)
    shutil.copytree(source_state, rust_state)

    python_result = _python_engine(
        project,
        python_state,
        "run",
        "session-continuity",
        "session-r38",
        "--token-budget",
        "64",
    )
    rust_result = _rust_engine(
        project,
        rust_state,
        "run",
        "session-continuity",
        "session-r38",
        "--token-budget",
        "64",
    )

    assert _normalize_dynamic(rust_result) == _normalize_dynamic(python_result)
    assert rust_result["root_summary_id"] is None
    assert rust_result["exact_recovery"] is True
