#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.util import stable_project_id


def _run(argv: list[str], *, cwd: Path, expected_codes: tuple[int, ...] = (0,)) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode not in expected_codes:
        raise RuntimeError(json.dumps({
            "argv": argv,
            "returncode": completed.returncode,
            "expected_codes": expected_codes,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }, ensure_ascii=False))
    if completed.returncode == 0:
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError(f"expected JSON object from {argv!r}: {value!r}")
        return completed.returncode, value
    return completed.returncode, {"stdout": completed.stdout, "stderr": completed.stderr}


def _prefix(kind: str, selector: Path, project: Path, state: Path) -> list[str]:
    common = ["--project", str(project), "--state-root", str(state)]
    if kind == "python":
        return [sys.executable, "-m", "syntavra_runtime", *common]
    return [str(selector), "--engine", "rust", *common]


def _make_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "MiXeD-Project"
    state = root / "state"
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    state.mkdir(parents=True)
    return project, state


def _normalize_plan(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    for key in ("cache_expires_at", "cache_refresh_after"):
        if key in result:
            result[key] = "<time>" if float(result[key] or 0) > 0 else 0.0
    reasons = []
    for reason in result.get("reasons") or []:
        text = str(reason)
        if text.startswith("cache-refresh-after:"):
            text = "cache-refresh-after:<time>"
        elif text.startswith("cache-expires-at:"):
            text = "cache-expires-at:<time>"
        reasons.append(text)
    result["reasons"] = reasons
    return result


def _audit(state: Path) -> list[dict[str, Any]]:
    path = state / "provider-gateway.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            "SELECT provider,model,request_hash,cache_key,request_handle,prompt_cache_mode,replay_cacheable FROM provider_request_audit ORDER BY sequence"
        ).fetchall()
    return [dict(row) for row in rows]


def _cache_rows(state: Path) -> list[dict[str, Any]]:
    path = state / "provider-gateway.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            "SELECT cache_key,provider,model,request_hash,request_handle,response_handle,response_hash,hit_count FROM provider_response_cache ORDER BY cache_key"
        ).fetchall()
    return [dict(row) for row in rows]


def _tables(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as database:
        rows = database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _normalize_cache_plan(state: Path) -> dict[str, Any]:
    path = state / "cache" / "plans.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("cache plans root must be object")
    value.pop("updated_at", None)
    plans = value.get("plans")
    if isinstance(plans, dict):
        for row in plans.values():
            if isinstance(row, dict):
                row["expires_at"] = "<time>"
                row["refresh_after"] = "<time>"
    return value


def _evidence_payload(state: Path, project: Path, handle: str) -> dict[str, Any]:
    store = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    payload = json.loads(store.get(handle).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("provider request evidence must be object")
    return payload


def _state_snapshot(state: Path, project: Path, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit": _audit(state),
        "response_cache": _cache_rows(state),
        "cache_plan": _normalize_cache_plan(state),
        "usage_tables": _tables(state / "usage-receipts.sqlite3"),
        "provider_tables": _tables(state / "provider-gateway.sqlite3"),
        "evidence_payload": _evidence_payload(state, project, str(plan["request_handle"])),
    }


def _scenario(
    root: Path,
    selector: Path,
    name: str,
    provider: str,
    request: dict[str, Any],
    extra: list[str] | None = None,
    *,
    use_input: bool = False,
    use_output: bool = False,
) -> dict[str, Any]:
    extra = list(extra or [])
    py_project, py_state = _make_fixture(root / f"python-{name}")
    rs_project, rs_state = _make_fixture(root / f"rust-{name}")

    def command(kind: str, project: Path, state: Path) -> tuple[list[str], Path | None]:
        args = [*_prefix(kind, selector, project, state), "provider", "prepare", provider]
        output = project / "prepare.json" if use_output else None
        if use_input:
            input_path = project / "request.json"
            input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            args += ["--input", str(input_path)]
        else:
            args += ["--request", json.dumps(request, ensure_ascii=False, separators=(",", ":"))]
        args += extra
        if output is not None:
            args += ["--output", "prepare.json"]
        return args, output

    py_argv, py_output = command("python", py_project, py_state)
    rs_argv, rs_output = command("rust", rs_project, rs_state)
    py_code, py_value = _run(py_argv, cwd=py_project)
    rs_code, rs_value = _run(rs_argv, cwd=rs_project)

    if use_output:
        assert py_output is not None and rs_output is not None
        py_file = json.loads(py_output.read_text(encoding="utf-8"))
        rs_file = json.loads(rs_output.read_text(encoding="utf-8"))
        py_plan = py_file
        rs_plan = rs_file
        wrapper_equal = py_value == rs_value
    else:
        py_plan = py_value
        rs_plan = rs_value
        wrapper_equal = True

    plan_equal = _normalize_plan(py_plan) == _normalize_plan(rs_plan)
    py_state_value = _state_snapshot(py_state, py_project, py_plan)
    rs_state_value = _state_snapshot(rs_state, rs_project, rs_plan)
    state_equal = py_state_value == rs_state_value

    return {
        "ok": py_code == rs_code == 0 and plan_equal and state_equal and wrapper_equal,
        "plan_equal": plan_equal,
        "state_equal": state_equal,
        "wrapper_equal": wrapper_equal,
        "python_plan": _normalize_plan(py_plan),
        "rust_plan": _normalize_plan(rs_plan),
        "python_state": py_state_value,
        "rust_state": rs_state_value,
    }


def _rejection(root: Path, selector: Path) -> dict[str, Any]:
    request = {
        "messages": [{"role": "user", "content": "no secrets"}],
        "headers": {"Authorization": "Bearer forbidden"},
    }
    py_project, py_state = _make_fixture(root / "python-reject")
    rs_project, rs_state = _make_fixture(root / "rust-reject")
    payload = json.dumps(request, separators=(",", ":"))
    py_code, _ = _run(
        [*_prefix("python", selector, py_project, py_state), "provider", "prepare", "openai", "--request", payload],
        cwd=py_project,
        expected_codes=tuple(range(1, 256)),
    )
    rs_code, _ = _run(
        [*_prefix("rust", selector, rs_project, rs_state), "provider", "prepare", "openai", "--request", payload],
        cwd=rs_project,
        expected_codes=tuple(range(1, 256)),
    )
    py_shape = {
        "audit": _audit(py_state),
        "provider_tables": _tables(py_state / "provider-gateway.sqlite3"),
        "usage_tables": _tables(py_state / "usage-receipts.sqlite3"),
        "evidence_exists": (py_state / "evidence").exists(),
    }
    rs_shape = {
        "audit": _audit(rs_state),
        "provider_tables": _tables(rs_state / "provider-gateway.sqlite3"),
        "usage_tables": _tables(rs_state / "usage-receipts.sqlite3"),
        "evidence_exists": (rs_state / "evidence").exists(),
    }
    return {
        "ok": py_code != 0 and rs_code != 0 and py_shape == rs_shape,
        "python_exit": py_code,
        "rust_exit": rs_code,
        "python_state": py_shape,
        "rust_state": rs_shape,
    }


def verify(selector: Path) -> dict[str, Any]:
    selector = selector.resolve(strict=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scenarios = {
            "openai_safe_reorder": _scenario(
                root,
                selector,
                "openai-reorder",
                "chatgpt",
                {
                    "model": "gpt-test",
                    "messages": [
                        {"role": "user", "content": "volatile tail"},
                        {"role": "system", "content": "stable system"},
                    ],
                    "temperature": 0,
                    "request_id": "volatile-id",
                },
                ["--cache-policy", "read-write", "--prompt-cache-ttl-seconds", "300"],
            ),
            "openai_input_output": _scenario(
                root,
                selector,
                "openai-io",
                "openai",
                {"model": "gpt-io", "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "tail"}], "temperature": False},
                ["--cache-policy", "read-write"],
                use_input=True,
                use_output=True,
            ),
            "anthropic": _scenario(
                root,
                selector,
                "anthropic",
                "claude",
                {"model": "claude-test", "system": "stable system", "messages": [{"role": "user", "content": "tail"}], "temperature": 0},
                ["--prompt-cache-ttl-seconds", "3600"],
            ),
            "gemini_explicit": _scenario(
                root,
                selector,
                "gemini",
                "google-ai",
                {"model": "gemini-test", "contents": [{"role": "user", "parts": [{"text": "hello"}]}], "generationConfig": {"temperature": 0}},
                ["--explicit-cache-name", "cachedContents/fixture"],
            ),
            "compatible_tools_disabled": _scenario(
                root,
                selector,
                "compatible-tools",
                "openrouter",
                {"model": "compat-test", "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "tail"}], "tools": [{"type": "function", "function": {"name": "x"}}], "temperature": 0},
            ),
            "cache_off": _scenario(
                root,
                selector,
                "cache-off",
                "openai",
                {"model": "off-test", "messages": [{"role": "System", "content": "case-sensitive role"}, {"role": "user", "content": "tail"}], "temperature": 0},
                ["--cache-policy", "off"],
            ),
        }
        rejection = _rejection(root, selector)
        ok = all(value["ok"] for value in scenarios.values()) and rejection["ok"]
        return {"ok": ok, "scenarios": scenarios, "credential_rejection": rejection}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify provider prepare Python-Rust CLI and state parity")
    parser.add_argument("--selector", required=True)
    args = parser.parse_args()
    result = verify(Path(args.selector))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
