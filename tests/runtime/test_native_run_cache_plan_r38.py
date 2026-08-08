from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run_cache_plan(
    engine: str,
    project: Path,
    state: Path,
    source: str,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-cache-plan-home"
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
            "cache-plan",
            source,
            *extra,
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


def _static(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: child
        for key, child in value.items()
        if key not in {"expires_at", "refresh_after"}
    }


def _assert_clock_contract(value: dict[str, Any]) -> None:
    ttl = float(value["ttl_seconds"])
    assert abs((value["expires_at"] - value["refresh_after"]) - ttl * 0.25) < 1e-6


def _both(
    project: Path,
    root: Path,
    source: str,
    *extra: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    python_state = root / "python-state"
    rust_state = root / "rust-state"
    python = _value(_run_cache_plan("python", project, python_state, source, *extra))
    rust = _value(_run_cache_plan("rust", project, rust_state, source, *extra))
    assert _static(rust) == _static(python)
    _assert_clock_contract(python)
    _assert_clock_contract(rust)
    python_now = python["expires_at"] - python["ttl_seconds"]
    rust_now = rust["expires_at"] - rust["ttl_seconds"]
    assert abs(rust_now - python_now) < 10.0
    return python, rust, python_state, rust_state


def _plan_file(state: Path) -> dict[str, Any]:
    return json.loads((state / "cache" / "plans.json").read_text(encoding="utf-8"))


def _persisted_static(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "plans": {
            key: _static(plan)
            for key, plan in value["plans"].items()
        }
    }


def test_native_cache_plan_reorders_stable_prefix_and_cleans_volatile_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    messages = [
        {"role": "user", "content": "volatile", "request_id": "drop-me"},
        {
            "role": "system",
            "content": "system rules",
            "timestamp": 123,
            "nested": {"nonce": "drop", "keep": "yes", "_private": "drop"},
        },
        {"role": "developer", "content": "developer rules"},
        {"role": "tool", "content": "tool schema", "cache_control": "stable"},
        {"role": "assistant", "content": "memo", "stable": True},
    ]
    source = json.dumps(messages, ensure_ascii=False)
    value, _, _, _ = _both(
        project,
        tmp_path,
        source,
        "--provider",
        "OpenAI",
        "--model",
        "gpt-test",
    )

    assert value["provider"] == "openai"
    assert value["model"] == "gpt-test"
    assert value["ttl_seconds"] == 600
    assert value["stable_messages"] == 4
    assert value["volatile_messages"] == 1
    assert value["reordered"] is True
    assert [row["role"] for row in value["segments"]] == [
        "system",
        "developer",
        "tool",
        "assistant",
        "user",
    ]
    assert [row["stable"] for row in value["segments"]] == [True, True, True, True, False]
    assert all(row["tokens_estimate"] >= 1 for row in value["segments"])
    assert all(len(row["content_hash"]) == 64 for row in value["segments"])
    assert len(value["stable_prefix_hash"]) == 64


def test_native_cache_plan_file_input_no_reorder_and_explicit_ttl_match_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    source = tmp_path / "messages.json"
    source.write_text(
        json.dumps(
            [
                {"role": "user", "content": "first"},
                {"role": "system", "content": "second"},
                {"role": "assistant", "content": "third", "cacheable": 1},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    value, _, _, _ = _both(
        project,
        tmp_path,
        str(source),
        "--provider",
        " Anthropic ",
        "--model",
        "claude-test",
        "--ttl",
        "17",
        "--no-reorder",
    )

    assert value["provider"] == "anthropic"
    assert value["ttl_seconds"] == 17
    assert value["reordered"] is False
    assert [row["role"] for row in value["segments"]] == ["user", "system", "assistant"]
    assert [row["stable"] for row in value["segments"]] == [False, True, True]


def test_native_cache_plan_zero_ttl_uses_provider_default_like_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    value, _, _, _ = _both(
        project,
        tmp_path,
        '[{"role":"system","content":"x"}]',
        "--provider=google",
        "--model=gemini-test",
        "--ttl=0",
    )
    assert value["ttl_seconds"] == 3600


def test_native_cache_plan_persistence_matches_python_and_retains_prior_keys(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    first_source = '[{"role":"system","content":"one"}]'
    _, _, python_state, rust_state = _both(
        project,
        tmp_path,
        first_source,
        "--provider",
        "openai",
        "--model",
        "m1",
    )

    second_source = '[{"role":"developer","content":"two"},{"role":"user","content":"tail"}]'
    python_second = _value(
        _run_cache_plan(
            "python",
            project,
            python_state,
            second_source,
            "--provider",
            "anthropic",
            "--model",
            "m2",
        )
    )
    rust_second = _value(
        _run_cache_plan(
            "rust",
            project,
            rust_state,
            second_source,
            "--provider",
            "anthropic",
            "--model",
            "m2",
        )
    )
    assert _static(rust_second) == _static(python_second)

    python_file = _plan_file(python_state)
    rust_file = _plan_file(rust_state)
    assert len(python_file["plans"]) == 2
    assert len(rust_file["plans"]) == 2
    assert _persisted_static(rust_file) == _persisted_static(python_file)
    assert isinstance(python_file["updated_at"], float)
    assert isinstance(rust_file["updated_at"], float)


def test_native_cache_plan_repeated_provider_model_and_ttl_last_value_wins(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    value, _, _, _ = _both(
        project,
        tmp_path,
        '[]',
        "--provider",
        "openai",
        "--provider=anthropic",
        "--model",
        "first",
        "--model=second",
        "--ttl",
        "1",
        "--ttl=9",
    )
    assert value["provider"] == "anthropic"
    assert value["model"] == "second"
    assert value["ttl_seconds"] == 9


def test_native_cache_plan_rejects_non_list_and_non_object_messages_like_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    for source in ('{"role":"system"}', '["not-an-object"]'):
        for engine in ("python", "rust"):
            result = _run_cache_plan(
                engine,
                project,
                tmp_path / f"{engine}-invalid-{abs(hash(source))}",
                source,
                "--provider",
                "openai",
                "--model",
                "m",
            )
            assert result.returncode != 0
