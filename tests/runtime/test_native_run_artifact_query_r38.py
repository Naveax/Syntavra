from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary
from tests.runtime.test_native_run_artifact_put_r38 import _run as _put_run
from tests.runtime.test_native_run_artifact_put_r38 import _value as _put_value

ROOT = Path(__file__).resolve().parents[2]


def _run_query(
    engine: str,
    project: Path,
    state: Path,
    artifact_id: str,
    *options: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-artifact-query-home"
    home.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        HOME=str(home),
        USERPROFILE=str(home),
        PATH="",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state),
            "run",
            "artifact-query",
            artifact_id,
            *options,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _value(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def _put(project: Path, state: Path, payload: str, *, engine: str = "python") -> dict[str, Any]:
    return _put_value(_put_run(engine, project, state, payload))


def _both(
    project: Path,
    state: Path,
    artifact_id: str,
    *options: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    python = _value(_run_query("python", project, state, artifact_id, *options))
    rust = _value(_run_query("rust", project, state, artifact_id, *options))
    assert rust == python
    return python, rust


def test_native_artifact_query_head_tail_and_limit_clamp_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(project, state, "zero\none\ntwo\nthree\nfour")

    head, _ = _both(project, state, record["artifact_id"], "--mode", "head", "--limit", "2")
    assert head["matched_lines"] == 2
    assert head["view"] == "zero\none"

    tail, _ = _both(project, state, record["artifact_id"], "--mode=tail", "--limit=2")
    assert tail["matched_lines"] == 2
    assert tail["view"] == "three\nfour"

    clamped, _ = _both(project, state, record["artifact_id"], "--limit", "0")
    assert clamped["matched_lines"] == 1
    assert clamped["view"] == "zero"


def test_native_artifact_query_errors_and_failures_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = "\n".join(
        [
            "normal line",
            "ERROR request failed",
            "src/main.rs:42:7 diagnostic",
            "panic happened",
            "assert condition",
            "normal tail",
        ]
    )
    record = _put(project, state, payload)

    errors, _ = _both(project, state, record["artifact_id"], "--mode", "errors")
    assert errors["view"].splitlines() == [
        "ERROR request failed",
        "src/main.rs:42:7 diagnostic",
        "panic happened",
    ]

    failures, _ = _both(project, state, record["artifact_id"], "--mode", "failures")
    assert failures["view"].splitlines() == [
        "ERROR request failed",
        "panic happened",
        "assert condition",
    ]


def test_native_artifact_query_regex_and_repeated_options_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(project, state, "drop\nkeep-10\nkeep-20\ndrop-30")
    value, _ = _both(
        project,
        state,
        record["artifact_id"],
        "--mode",
        "head",
        "--mode",
        "regex",
        "--expression",
        "ignored",
        "--expression=^keep-[0-9]+$",
        "--limit",
        "1",
        "--limit=10",
    )
    assert value["mode"] == "regex"
    assert value["expression"] == "^keep-[0-9]+$"
    assert value["view"] == "keep-10\nkeep-20"


def test_native_artifact_query_json_mapping_array_and_negative_index_match_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payload = json.dumps(
        {
            "z": 1,
            "nested": {
                "items": [
                    {"name": "first", "value": 1},
                    {"value": 2, "name": "last"},
                ]
            },
        },
        ensure_ascii=False,
    )
    record = _put(project, state, payload)

    selected, _ = _both(
        project,
        state,
        record["artifact_id"],
        "--mode",
        "json",
        "--expression",
        "nested.items.-1",
    )
    assert selected["view"] == '{\n  "name": "last",\n  "value": 2\n}'
    assert selected["matched_lines"] == 4

    scalar, _ = _both(
        project,
        state,
        record["artifact_id"],
        "--mode=json",
        "--expression=nested.items.0.name",
    )
    assert scalar["view"] == '"first"'
    assert scalar["matched_lines"] == 1


def test_native_artifact_query_redaction_and_token_estimate_match_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(
        project,
        state,
        "api_key = abc123\nauthorization: BearerToken\npassword=hunter2\nvisible",
    )
    value, _ = _both(project, state, record["artifact_id"], "--limit", "20")
    assert "abc123" not in value["view"]
    assert "BearerToken" not in value["view"]
    assert "hunter2" not in value["view"]
    assert value["view"].count("<redacted>") == 3
    assert value["view_tokens"] >= 1


def test_native_artifact_query_python_splitlines_boundaries_match(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(project, state, "a\rb\u000bc\u000cd\u001ce\u001df\u001eg\u0085h\u2028i\u2029j")
    value, _ = _both(project, state, record["artifact_id"], "--limit", "20")
    assert value["matched_lines"] == 10
    assert value["view"].split("\n") == list("abcdefghij")


def test_native_artifact_query_rust_written_artifact_is_python_queryable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(project, state, "rust-first\nsecond", engine="rust")
    value, _ = _both(project, state, record["artifact_id"], "--mode", "tail", "--limit", "1")
    assert value["view"] == "second"
    assert value["artifact"] == record


def test_native_artifact_query_missing_and_corrupt_artifacts_fail_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    missing = "sha256:" + ("0" * 64)
    for engine in ("python", "rust"):
        result = _run_query(engine, project, state, missing)
        assert result.returncode != 0

    record = _put(project, state, "original")
    Path(record["object_path"]).write_text("tampered", encoding="utf-8")
    for engine in ("python", "rust"):
        result = _run_query(engine, project, state, record["artifact_id"])
        assert result.returncode != 0


def test_native_artifact_query_invalid_regex_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    record = _put(project, state, "value")
    for engine in ("python", "rust"):
        result = _run_query(
            engine,
            project,
            state,
            record["artifact_id"],
            "--mode",
            "regex",
            "--expression",
            "[",
        )
        assert result.returncode != 0
