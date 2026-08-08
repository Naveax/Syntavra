from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


def _run(
    engine: str,
    project: Path,
    state_root: Path,
    codex_home: Path,
    session_hint: str,
) -> tuple[int, Any]:
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    completed = subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state_root),
            "--codex-home",
            str(codex_home),
            "rollout-tail",
            "--session-hint",
            session_hint,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    return completed.returncode, json.loads(completed.stdout)


def _event(identifier: str, input_tokens: int) -> bytes:
    return (
        json.dumps(
            {
                "id": identifier,
                "type": "response.completed",
                "input_tokens": input_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )


def test_rollout_selector_options_match_python_and_rust(tmp_path: Path) -> None:
    project = tmp_path / "project"
    codex_home = tmp_path / "codex"
    sessions = codex_home / "sessions" / "2026" / "08"
    project.mkdir()
    sessions.mkdir(parents=True)

    selected = sessions / "rollout-session-needle.jsonl"
    selected.write_bytes(_event("selected", 17))

    # Ensure the hinted file is not selected merely because it is newest.
    time.sleep(0.02)
    newest = sessions / "rollout-other-session.jsonl"
    newest.write_bytes(_event("newest", 999))
    newer = time.time() + 2.0
    os.utime(newest, (newer, newer))

    python_result = _run(
        "python",
        project,
        tmp_path / "python-state",
        codex_home,
        "session-needle",
    )
    rust_result = _run(
        "rust",
        project,
        tmp_path / "rust-state",
        codex_home,
        "session-needle",
    )

    assert rust_result == python_result
    code, payload = rust_result
    assert code == 0
    assert Path(payload["rollout"]).name == selected.name
    assert payload["processed_events"] == 1
    assert payload["counters"]["fresh_input_tokens"] == 17
