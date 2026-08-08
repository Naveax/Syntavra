from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.util import stable_project_id
from tests.runtime.test_native_install_r38 import _assert_equal, _normalized, _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    phase: str,
    payload: dict[str, Any],
    *,
    stdin: bool = False,
) -> tuple[int, Any, str]:
    state = project / "state"
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    command = [
        *prefix,
        "--engine",
        engine,
        "--project",
        str(project),
        "--state-root",
        str(state),
        "hook",
        phase,
    ]
    input_text = json.dumps(payload, sort_keys=True)
    if not stdin:
        command.extend(["--payload", input_text])
        input_text = ""
    environment = os.environ.copy()
    environment["HOME"] = str(project / "home")
    environment["USERPROFILE"] = environment["HOME"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=input_text,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )
    assert completed.stdout.strip(), {
        "engine": engine,
        "phase": phase,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
    }
    return completed.returncode, json.loads(completed.stdout), completed.stderr


def _normalize(value: Any, project: Path) -> Any:
    value = _normalized(value, project)
    if isinstance(value, dict) and "timestamp" in value:
        value = {**value, "timestamp": "<timestamp>"}
    return value


def _assert_pair(
    tmp_path: Path,
    phase: str,
    payload: dict[str, Any],
    *,
    stdin: bool = False,
) -> Any:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    python_result = _run("python", python_project, phase, payload, stdin=stdin)
    rust_result = _run("rust", rust_project, phase, payload, stdin=stdin)
    assert rust_result[0] == python_result[0] == 0, {
        "python": python_result,
        "rust": rust_result,
    }
    assert rust_result[2] == python_result[2] == ""
    _assert_equal(
        _normalize(rust_result[1], rust_project),
        _normalize(python_result[1], python_project),
        f"hook-{phase}",
    )
    return rust_result[1]


def test_native_hook_pre_non_shell_pass_through_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(
        tmp_path,
        "pre",
        {"tool": "read", "command": ["cat", "README.md"]},
    )
    assert value["allowed"] is True
    assert value["mode"] == "pass-through"


def test_native_hook_pre_destructive_command_is_blocked(tmp_path: Path) -> None:
    value = _assert_pair(
        tmp_path,
        "pre",
        {"tool": "shell", "command": ["rm", "-rf", "/"]},
    )
    assert value["allowed"] is False
    assert value["reasons"] == ["destructive-command"]


def test_native_hook_session_start_empty_task_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(tmp_path, "session-start", {})
    assert value["optimization_mode"] == "full"
    assert value["statusline"] == "[SYN:FULL] ⇩0"
    assert value["delegation"] is None


def test_native_hook_empty_prompt_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(tmp_path, "prompt", {})
    assert value["risk"] == "normal"
    assert value["prompt_bytes"] == 0
    assert value["memory_observations"] == []


def test_native_hook_pre_compact_supports_stdin(tmp_path: Path) -> None:
    value = _assert_pair(
        tmp_path,
        "pre-compact",
        {"session_id": "session-a"},
        stdin=True,
    )
    assert value["mode"] == "checkpoint-before-compact"


def test_native_hook_post_compact_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(tmp_path, "post-compact", {"session_id": "session-a"})
    assert value["checks"] == [
        "history-chain",
        "summary-expansion",
        "mandatory-context-roles",
    ]


def test_native_hook_stop_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(tmp_path, "stop", {"session_id": "session-a"})
    assert value["actions"][-1] == "flush-fabric-insights"


def test_native_hook_session_end_without_summary_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(tmp_path, "session-end", {"session_id": "session-a"})
    assert value["memory_observations"] == []
    assert value["memory_extraction_error"] == ""


def test_native_hook_post_scalar_pass_through_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(
        tmp_path,
        "post",
        {"tool": "shell", "command": ["true"], "result": 7},
    )
    assert value == {
        "mode": "pass-through",
        "result": 7,
        "captures": [],
        "usage_receipt_hash": None,
        "usage_chain_hash": None,
    }


def test_native_hook_post_small_exact_capture_matches_python(tmp_path: Path) -> None:
    payload = {
        "tool": "shell",
        "command": ["printf", "hello-hook"],
        "result": "hello-hook",
    }
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()

    python_code, python_value, python_stderr = _run("python", python_project, "post", payload)
    rust_code, rust_value, rust_stderr = _run("rust", rust_project, "post", payload)
    assert rust_code == python_code == 0
    assert rust_stderr == python_stderr == ""
    _assert_equal(rust_value, python_value, "hook-post-small-capture")

    capture = rust_value["captures"][0]
    assert capture["path"] == "result"
    assert capture["family"] == "text"
    assert capture["mode"] == "passthrough-captured"
    assert capture["merkle_root"] == capture["exact_handle"].removeprefix("sc://sha256/")
    assert rust_value["result"] == "hello-hook"

    store = EvidenceStore(
        rust_project / "state" / "evidence",
        project_id=stable_project_id(rust_project),
    )
    assert store.get(capture["exact_handle"]) == b"hello-hook"

    with sqlite3.connect(rust_project / "state" / "tool-externalization.sqlite3") as db:
        row = db.execute(
            "SELECT artifact_id,content_hash,segment_count,quality_gate_passed "
            "FROM ext_artifacts WHERE artifact_id=?",
            (capture["artifact_id"],),
        ).fetchone()
    assert row == (
        capture["artifact_id"],
        capture["merkle_root"],
        1,
        1,
    )


def test_native_hook_post_duplicate_reference_matches_python(tmp_path: Path) -> None:
    payload = {
        "tool": "shell",
        "command": ["printf", "duplicate-hook"],
        "result": "duplicate-hook",
    }
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()

    first_python = _run("python", python_project, "post", payload)
    first_rust = _run("rust", rust_project, "post", payload)
    second_python = _run("python", python_project, "post", payload)
    second_rust = _run("rust", rust_project, "post", payload)

    assert first_python[0] == first_rust[0] == second_python[0] == second_rust[0] == 0
    _assert_equal(first_rust[1], first_python[1], "hook-post-first-capture")
    _assert_equal(second_rust[1], second_python[1], "hook-post-duplicate")
    assert second_rust[1]["captures"][0]["mode"] == "dedup-reference"
    assert "seen=2" in second_rust[1]["result"]


def test_native_hook_post_skip_tool_prefix_matches_python(tmp_path: Path) -> None:
    value = _assert_pair(
        tmp_path,
        "post",
        {
            "tool": "syntavra.output.verify",
            "result": "do-not-reexternalize",
        },
    )
    assert value["mode"] == "pass-through"
    assert value["captures"] == []
