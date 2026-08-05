from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    messages: list[str | dict[str, Any]],
    *,
    profile: str | None = None,
    schema_mode: str | None = None,
) -> tuple[int, list[dict[str, Any]], str]:
    state = project / "state"
    skill = project / "skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("name: syntavra\n", encoding="utf-8")
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
        "--skill-root",
        str(skill),
        "--codex-home",
        str(project / ".codex"),
        "--host",
        "codex",
        "mcp",
    ]
    payload = "\n".join(
        row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)
        for row in messages
    ) + "\n"
    environment = os.environ.copy()
    environment["HOME"] = str(project / "home")
    environment["USERPROFILE"] = environment["HOME"]
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    for key, value in (
        ("SYNTAVRA_MCP_PROFILE", profile),
        ("SYNTAVRA_SCHEMA_MODE", schema_mode),
    ):
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=payload,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )
    output = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return completed.returncode, output, completed.stderr


def _request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def _call(name: str, arguments: dict[str, Any] | None = None, request_id: int = 1) -> dict[str, Any]:
    return _request(
        "tools/call",
        request_id,
        {"name": name, "arguments": arguments or {}},
    )


def _pair(
    tmp_path: Path,
    messages: list[str | dict[str, Any]],
    *,
    profile: str | None = None,
    schema_mode: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_project.mkdir()
    rust_project.mkdir()
    python_result = _run(
        "python",
        python_project,
        messages,
        profile=profile,
        schema_mode=schema_mode,
    )
    rust_result = _run(
        "rust",
        rust_project,
        messages,
        profile=profile,
        schema_mode=schema_mode,
    )
    assert rust_result[0] == python_result[0] == 0, {
        "python": python_result,
        "rust": rust_result,
    }
    assert rust_result[2] == python_result[2] == ""
    return python_result[1], rust_result[1]


def test_native_mcp_stream_lifecycle_matches_python(tmp_path: Path) -> None:
    messages = [
        _request("initialize", 1, {"protocolVersion": "2026-01-01"}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _request("ping", 2),
        _request("unknown/method", 3),
    ]
    python_value, rust_value = _pair(tmp_path, messages)
    assert rust_value == python_value
    assert len(rust_value) == 3


def test_native_mcp_parse_error_matches_python(tmp_path: Path) -> None:
    python_value, rust_value = _pair(tmp_path, ["{"])
    assert rust_value == python_value
    assert rust_value[0]["error"]["code"] == -32700


def test_native_mcp_compiled_tool_lists_match_all_profiles(tmp_path: Path) -> None:
    expected = {"minimal": 8, "balanced": 36}
    for profile in ("minimal", "balanced", "audit"):
        python_value, rust_value = _pair(
            tmp_path / profile,
            [_request("tools/list", 1)],
            profile=profile,
        )
        assert rust_value == python_value, {"profile": profile}
        tools = rust_value[0]["result"]["tools"]
        if profile in expected:
            assert len(tools) == expected[profile]
        else:
            assert len(tools) > expected["balanced"]


def test_native_mcp_raw_tool_list_matches_python(tmp_path: Path) -> None:
    python_value, rust_value = _pair(
        tmp_path,
        [_request("tools/list", 1)],
        profile="minimal",
        schema_mode="raw",
    )
    assert rust_value == python_value
    assert len(rust_value[0]["result"]["tools"]) == 8


def test_native_mcp_hidden_tool_denial_matches_python(tmp_path: Path) -> None:
    python_value, rust_value = _pair(
        tmp_path,
        [_call("syntavra.evidence.rotate_key")],
        profile="minimal",
    )
    assert rust_value == python_value
    assert rust_value[0]["error"]["data"]["reason"] == "tool-not-exposed-by-active-profile"


def test_native_mcp_audit_destructive_denial_matches_python(tmp_path: Path) -> None:
    python_value, rust_value = _pair(
        tmp_path,
        [_call("syntavra.evidence.rotate_key", {"reencrypt": True})],
        profile="audit",
    )
    assert rust_value == python_value
    assert rust_value[0]["error"]["data"]["risk"] == "destructive"


def test_native_mcp_exact_evidence_denial_matches_python(tmp_path: Path) -> None:
    python_value, rust_value = _pair(
        tmp_path,
        [
            _call(
                "syntavra.session.open",
                {
                    "session_id": "denied",
                    "_syntavra_authorization": {"exact_evidence": False},
                },
            )
        ],
        profile="audit",
    )
    assert rust_value == python_value
    assert rust_value[0]["error"]["data"]["reason"] == "exact-evidence-required"


def test_native_mcp_unsandboxed_process_denial_matches_python(tmp_path: Path) -> None:
    arguments = {
        "_syntavra_authorization": {
            "user_authorized": True,
            "exact_evidence": True,
            "sandboxed": False,
        },
        "argv": ["python", "-c", "print('must not execute')"],
    }
    python_value, rust_value = _pair(
        tmp_path,
        [_call("syntavra.process.submit", arguments)],
        profile="balanced",
    )
    assert rust_value == python_value
    assert rust_value[0]["error"]["data"]["reason"] == "unsandboxed-process-disabled"


def test_native_mcp_installed_profile_file_is_default(tmp_path: Path) -> None:
    messages = [_request("tools/list", 1)]
    results: dict[str, list[dict[str, Any]]] = {}
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        state = project / "state"
        project.mkdir()
        state.mkdir()
        (state / "mcp-profile.json").write_text(
            '{"name":"balanced","max_active_tools":36}',
            encoding="utf-8",
        )
        code, output, stderr = _run(engine, project, messages)
        assert code == 0 and stderr == ""
        results[engine] = output
    assert results["rust"] == results["python"]
    assert len(results["rust"][0]["result"]["tools"]) == 36


def test_native_mcp_safe_status_call_has_matching_policy_metadata(tmp_path: Path) -> None:
    python_value, rust_value = _pair(
        tmp_path,
        [_call("syntavra.status")],
        profile="minimal",
    )
    python_response = python_value[0]
    rust_response = rust_value[0]
    assert "result" in python_response and "result" in rust_response
    python_meta = python_response["result"]["_meta"]
    rust_meta = rust_response["result"]["_meta"]
    for key in (
        "syntavra_route_receipt",
        "syntavra_profile",
        "syntavra_risk",
        "syntavra_schema_mode",
        "syntavra_schema_compilation",
        "syntavra_wire",
    ):
        assert rust_meta[key] == python_meta[key], key
    assert rust_meta["syntavra_profile"] == "minimal"
    assert rust_meta["syntavra_risk"] == "read-or-plan"
    assert json.loads(rust_response["result"]["content"][0]["text"])
