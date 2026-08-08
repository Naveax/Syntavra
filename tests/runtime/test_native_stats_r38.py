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


def _python_stats(state_root: Path) -> Any:
    return _json_command(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--state-root",
            str(state_root),
            "stats",
        ]
    )


def _rust_stats(state_root: Path) -> Any:
    return _json_command(
        [
            str(_selector_binary()),
            "--engine",
            "rust",
            "--state-root",
            str(state_root),
            "stats",
        ]
    )


def test_native_empty_stats_match_python_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    rust_result = _rust_stats(state_root)
    python_result = _python_stats(state_root)
    assert rust_result == python_result
    assert rust_result["installed"] is False
    assert rust_result["onboarding"]["claim"] == "ONBOARDING_NOT_MEASURED"
    assert rust_result["session_analytics"]["events"] == 0
    assert rust_result["savings_receipts"] == 0
    assert rust_result["provider_usage_integrity"] == {
        "attestation": "HASH_CHAIN_ONLY",
        "entries": 0,
        "last_chain_hash": "0" * 64,
        "ok": True,
        "reasons": [],
    }


def test_native_populated_stats_match_python_exactly(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    analytics = state_root / "analytics"
    analytics.mkdir(parents=True)
    rows = [
        {
            "session_id": "session-a",
            "repository_hash": "repo-a",
            "input_tokens": "120",
            "cached_input_tokens": 20,
            "output_tokens": 30.9,
            "wall_time_ms": 10.5,
            "cost_usd": 0.25,
            "compaction_ms": 3,
            "continuity_restored": True,
            "tool_route_allowed": False,
        },
        {
            "session_id": "session-a",
            "repository_hash": "repo-b",
            "input_tokens": -5,
            "cached_input_tokens": True,
            "output_tokens": "7",
            "wall_time_ms": -9,
            "cost_usd": "0.5",
            "compaction_ms": 0.25,
            "continuity_restored": [1],
            "tool_route_allowed": True,
        },
        {
            "session_id": "",
            "repository_hash": None,
            "input_tokens": 10,
            "cached_input_tokens": 1000,
            "output_tokens": 0,
        },
    ]
    (analytics / "events.jsonl").write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )

    rust_result = _rust_stats(state_root)
    python_result = _python_stats(state_root)

    assert rust_result == python_result
    analytics_result = rust_result["session_analytics"]
    assert analytics_result["events"] == 3
    assert analytics_result["sessions"] == 1
    assert analytics_result["repositories"] == 2
    assert analytics_result["usage"]["input_tokens"] == 130
    assert analytics_result["usage"]["cached_input_tokens"] == 1021
    assert analytics_result["usage"]["billable_input_tokens"] == 0
    assert analytics_result["continuity"]["restores"] == 2
    assert analytics_result["routing"]["denied"] == 1
