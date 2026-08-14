from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
ZERO_HASH = "0" * 64


def _json_command(argv: list[str]) -> Any:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
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


def _event_hash(event: dict[str, Any]) -> str:
    material = {
        "session_id": event["session_id"],
        "sequence": event["sequence"],
        "event_type": event["event_type"],
        "payload": event["payload"],
        "previous_hash": event["previous_hash"],
        "created_at": event["created_at"],
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_open(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    result["session"]["created_at"] = "<dynamic>"
    result["session"]["updated_at"] = "<dynamic>"
    return result


def test_native_session_open_matches_python_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    metadata = json.dumps(
        {"owner": "r38", "labels": ["native", "session"]},
        sort_keys=True,
        separators=(",", ":"),
    )

    python_result = _python_engine(
        project,
        tmp_path / "python-state",
        "run",
        "session-open",
        "--session-id",
        "session-r38",
        "--metadata",
        metadata,
    )
    rust_result = _rust_engine(
        project,
        tmp_path / "rust-state",
        "run",
        "session-open",
        "--session-id",
        "session-r38",
        "--metadata",
        metadata,
    )

    assert _normalize_open(rust_result) == _normalize_open(python_result)
    assert rust_result["continuity_restored"] is False
    assert rust_result["verification"] == {
        "ok": True,
        "events": 0,
        "last_hash": ZERO_HASH,
        "reasons": [],
    }


def test_rust_append_is_accepted_by_python_continuity_verifier(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    _python_engine(
        project,
        state,
        "run",
        "session-open",
        "--session-id",
        "session-r38",
    )

    payload = json.dumps(
        {"task": "native append", "result": {"ok": True}, "unicode": "çalışıyor"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    appended = _rust_engine(
        project,
        state,
        "run",
        "session-append",
        "session-r38",
        "tool-result",
        payload,
    )
    event = appended["event"]
    assert event["sequence"] == 1
    assert event["previous_hash"] == ZERO_HASH
    assert event["event_hash"] == _event_hash(event)

    continuity = _python_engine(
        project,
        state,
        "run",
        "session-continuity",
        "session-r38",
        "--token-budget",
        "32000",
    )
    assert continuity["continuity_restored"] is True
    assert continuity["exact_recovery"] is True
    assert continuity["last_event_hash"] == event["event_hash"]
    assert continuity["claim"] == "SESSION_CONTINUITY_INTERNALLY_VERIFIED"


def test_python_append_is_readable_by_native_session_status(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    _rust_engine(
        project,
        state,
        "run",
        "session-open",
        "--session-id",
        "session-r38",
    )
    appended = _python_engine(
        project,
        state,
        "run",
        "session-append",
        "session-r38",
        "decision",
        json.dumps({"decision": "continue"}, separators=(",", ":")),
    )
    assert appended["event"]["event_hash"] == _event_hash(appended["event"])

    status = _rust_engine(project, state, "run", "session-status")
    assert status["sessions"][0]["session_id"] == "session-r38"
    assert status["analytics"]["events"] == 2
    assert status["analytics"]["sessions"] == 1
