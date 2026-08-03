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


def _python_record(state_root: Path, event: str) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--state-root",
            str(state_root),
            "run",
            "record",
            event,
        ]
    )


def _rust_record(state_root: Path, event: str) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--state-root",
            str(state_root),
            "run",
            "record",
            event,
        ]
    )


def test_native_analytics_record_matches_python_jsonl_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    event = {
        "observed_at": "2026-08-03T09:00:00+00:00",
        "session_id": "session-r38",
        "repository_hash": "a" * 64,
        "provider": "openai",
        "model": "gpt-test",
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "output_tokens": 30,
        "wall_time_ms": 125.5,
        "cost_usd": 0.0125,
        "quality_score": 0.95,
        "success": True,
        "metadata": {"source": "r38"},
        "prompt_content": "must-not-be-persisted",
    }
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    python_result = _python_record(state_root, encoded)
    analytics_path = state_root / "analytics" / "events.jsonl"
    python_bytes = analytics_path.read_bytes()
    shutil.rmtree(state_root)

    rust_result = _rust_record(state_root, encoded)
    rust_bytes = analytics_path.read_bytes()

    assert rust_result == python_result
    assert rust_bytes == python_bytes
    row = json.loads(rust_bytes)
    assert row["schema_version"] == 1
    assert row["kind"] == "agent-turn"
    assert "prompt_content" not in row


def test_native_analytics_preserves_explicit_empty_event_id(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    event = json.dumps(
        {
            "event_id": "",
            "observed_at": "2026-08-03T09:00:00+00:00",
            "kind": "test",
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    python_result = _python_record(state_root, event)
    python_bytes = (state_root / "analytics" / "events.jsonl").read_bytes()
    shutil.rmtree(state_root)

    rust_result = _rust_record(state_root, event)
    rust_bytes = (state_root / "analytics" / "events.jsonl").read_bytes()

    assert rust_result == python_result
    assert rust_bytes == python_bytes
    assert rust_result["event_id"] == ""
