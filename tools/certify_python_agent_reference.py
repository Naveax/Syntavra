#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


PATCH = """diff --git a/sample.py b/sample.py
--- a/sample.py
+++ b/sample.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
VERIFIER = ["/bin/sh", "-c", "grep -q 'VALUE = 2' sample.py"]
EXPECTED_PRODUCT_KEYS = {
    "delivery",
    "delivery_options",
    "events",
    "limitations",
    "model",
    "ok",
    "post_verifiers",
    "provider",
    "run",
    "tool_trace",
    "usage",
    "verification_complete",
    "verifier",
}
EXPECTED_RUN_KEYS = {
    "attempts",
    "changed_files",
    "context",
    "duration_ms",
    "final_diff",
    "finished_at",
    "rollback_complete",
    "run_id",
    "started_at",
    "state",
    "stop_reason",
    "task",
    "total_cost",
    "total_tokens",
    "workspace",
}
EXPECTED_EVENT_KEYS = {"created_at", "event_type", "payload", "sequence"}
EXPECTED_REQUEST_KEYS = {"max_tokens", "messages", "model", "stream", "temperature"}


def _git(*args: str, cwd: Path) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr}")


def prepare_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "Makefile").write_text(
        "test:\n\tgrep -q 'VALUE = 2' sample.py\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# Python agent reference fixture\n\nChange VALUE from 1 to 2.\n",
        encoding="utf-8",
    )
    _git("init", "-q", cwd=project)
    _git("config", "user.name", "Syntavra Python Reference", cwd=project)
    _git("config", "user.email", "syntavra-python-reference@example.invalid", cwd=project)
    _git("add", ".", cwd=project)
    _git("commit", "-qm", "fixture", cwd=project)


def run_python(
    repo: Path,
    project: Path,
    state_root: Path,
    args: list[str],
    *,
    process_timeout: float = 20.0,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "syntavra_runtime.engine_entry",
        "--engine",
        "python",
        "--project",
        str(project),
        "--state-root",
        str(state_root),
        *args,
    ]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    # The Python reference must not depend on Rust parity/probe activation.
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=process_timeout,
        check=False,
    )
    try:
        value = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {
        "exit": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "value": value,
    }


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address


class MockEndpoint:
    def __init__(
        self,
        contents: list[str] | None = None,
        *,
        status: int = 200,
        raw_body: bytes | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.contents = list(contents or [])
        self.status = int(status)
        self.raw_body = raw_body
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                owner.requests.append(
                    {
                        "path": self.path,
                        "body": body,
                        "content_type": self.headers.get("Content-Type"),
                        "authorization": self.headers.get("Authorization"),
                        "user_agent": self.headers.get("User-Agent"),
                    }
                )
                if owner.delay_seconds:
                    time.sleep(owner.delay_seconds)
                if owner.status != 200:
                    encoded = json.dumps({"error": "fixture failure"}).encode("utf-8")
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    try:
                        self.wfile.write(encoded)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if owner.raw_body is not None:
                    encoded = owner.raw_body
                else:
                    index = len(owner.requests) - 1
                    if index >= len(owner.contents):
                        content = owner.contents[-1] if owner.contents else ""
                    else:
                        content = owner.contents[index]
                    response = {
                        "id": f"mock-{index + 1}",
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 7,
                            "completion_tokens": 5,
                            "total_tokens": 12,
                        },
                    }
                    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self.server = _QuietServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "MockEndpoint":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _closed_local_endpoint() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return f"http://127.0.0.1:{port}/v1"


def _agent_run_args(endpoint: str, *, timeout: str = "5") -> list[str]:
    return [
        "agent",
        "run",
        "change VALUE to 2",
        "--provider",
        "openai-compatible",
        "--model",
        "mock-model",
        "--endpoint",
        endpoint,
        "--api-mode",
        "chat",
        "--mode",
        "safe-autonomous",
        "--authorized",
        "--attempts",
        "1",
        "--timeout",
        timeout,
        "--delivery",
        "diff",
        "--no-post-verifiers",
    ]


def _failure_summary(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("value")
    code = None
    detail = None
    if isinstance(value, dict):
        error = value.get("error") if isinstance(value.get("error"), dict) else {}
        code = error.get("code")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        detail = details.get("error")
    return {
        "exit": result["exit"],
        "stdout_format": "json-object" if isinstance(value, dict) else "empty" if not result["stdout"] else "other",
        "stderr_empty": not bool(result["stderr"]),
        "error_code": code,
        "detail": detail,
    }


def _assert_public_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    summary = _failure_summary(result)
    if result["exit"] != 4:
        raise AssertionError(f"{label}: expected exit 4, got {result}")
    value = result.get("value")
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: expected JSON failure envelope, got {result}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: wrong public error contract: {result}")
    if result["stderr"]:
        raise AssertionError(f"{label}: public domain failure leaked stderr: {result['stderr']!r}")
    return summary


def _assert_argparse_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2:
        raise AssertionError(f"{label}: expected argparse exit 2, got {result}")
    if result["stdout"]:
        raise AssertionError(f"{label}: argparse failure unexpectedly wrote stdout: {result['stdout']!r}")
    if "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: argparse failure did not emit usage: {result['stderr']!r}")
    return {
        "exit": 2,
        "stdout_format": "empty",
        "stderr_format": "argparse-usage-error",
    }


def _artifact_rows(state_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(state_root.rglob("agent-receipts/*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"path_suffix": f"agent-receipts/{path.name}", "value": value})
    return rows


def certify(repo: Path) -> dict[str, Any]:
    if shutil.which("git") is None or shutil.which("make") is None:
        raise RuntimeError("Python agent certification requires git and make")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="syntavra-python-agent-reference-") as directory:
        root = Path(directory)

        live_project = root / "live-project"
        live_state = root / "live-state"
        prepare_project(live_project)
        search_action = json.dumps({"action": "search", "query": "VALUE"}, separators=(",", ":"))
        patch_action = json.dumps(
            {"action": "patch", "patch": PATCH, "rationale": "deterministic Python reference"},
            separators=(",", ":"),
        )
        with MockEndpoint([search_action, patch_action]) as endpoint:
            live_result = run_python(
                repo,
                live_project,
                live_state,
                _agent_run_args(endpoint.endpoint),
            )
            requests = list(endpoint.requests)

        if live_result["exit"] != 0 or not isinstance(live_result["value"], dict):
            raise AssertionError(f"agent run happy path failed: {live_result}")
        if live_result["stderr"]:
            raise AssertionError(f"agent run happy path leaked stderr: {live_result['stderr']!r}")
        live = live_result["value"]
        if live.get("ok") is not True:
            raise AssertionError(f"agent run did not return ok=true: {live}")
        if set(live) != EXPECTED_PRODUCT_KEYS:
            raise AssertionError(f"agent product schema drift: {sorted(live)}")
        run = live.get("run")
        if not isinstance(run, dict) or set(run) != EXPECTED_RUN_KEYS:
            raise AssertionError(f"agent run receipt schema drift: {run}")
        events = live.get("events")
        if not isinstance(events, list) or not events:
            raise AssertionError("agent run did not emit events")
        if any(not isinstance(item, dict) or set(item) != EXPECTED_EVENT_KEYS for item in events):
            raise AssertionError(f"agent event schema drift: {events}")
        sequences = [item["sequence"] for item in events]
        if sequences != list(range(1, len(events) + 1)):
            raise AssertionError(f"agent event sequence drift: {sequences}")
        trace = live.get("tool_trace")
        if not isinstance(trace, list) or [item.get("action") for item in trace] != ["search", "patch"]:
            raise AssertionError(f"tool trace ordering drift: {trace}")
        if len(requests) != 2:
            raise AssertionError(f"expected two model rounds, got {requests}")
        request_bodies = [item.get("body") for item in requests]
        if any(not isinstance(item, dict) for item in request_bodies):
            raise AssertionError(f"non-object model request: {requests}")
        first = request_bodies[0]
        second = request_bodies[1]
        assert isinstance(first, dict) and isinstance(second, dict)
        if set(first) != EXPECTED_REQUEST_KEYS or set(second) != EXPECTED_REQUEST_KEYS:
            raise AssertionError(f"OpenAI-compatible request schema drift: {request_bodies}")
        if first.get("model") != "mock-model" or second.get("model") != "mock-model":
            raise AssertionError(f"model drift: {request_bodies}")
        if first.get("stream") is not False or first.get("temperature") != 0.1 or first.get("max_tokens") != 8192:
            raise AssertionError(f"generation request defaults drift: {first}")
        if "tools" in first or "tool_choice" in first or "tools" in second or "tool_choice" in second:
            raise AssertionError("Python agent unexpectedly changed from JSON-action protocol to API tool calling")
        first_messages = first.get("messages")
        second_messages = second.get("messages")
        if not isinstance(first_messages, list) or not isinstance(second_messages, list):
            raise AssertionError(f"messages schema drift: {request_bodies}")
        first_roles = [item.get("role") for item in first_messages if isinstance(item, dict)]
        second_roles = [item.get("role") for item in second_messages if isinstance(item, dict)]
        if first_roles != ["system", "user"]:
            raise AssertionError(f"first model role ordering drift: {first_roles}")
        if second_roles != ["system", "user", "assistant", "user"]:
            raise AssertionError(f"tool-result message ordering drift: {second_roles}")
        assistant_action = json.loads(str(second_messages[2].get("content") or "{}"))
        tool_result = json.loads(str(second_messages[3].get("content") or "{}"))
        if assistant_action.get("action") != "search" or tool_result.get("tool") != "repo.search":
            raise AssertionError(f"tool round-trip contract drift: {second_messages[2:4]}")
        system_prompt = str(first_messages[0].get("content") or "")
        if system_prompt != str(second_messages[0].get("content") or ""):
            raise AssertionError("system prompt changed between tool rounds")

        artifacts = _artifact_rows(live_state)
        if not artifacts:
            raise AssertionError("agent run did not persist a durable run receipt")
        for artifact in artifacts:
            value = artifact["value"]
            if not isinstance(value, dict) or set(value) != EXPECTED_RUN_KEYS:
                raise AssertionError(f"durable agent receipt schema drift: {artifact}")

        cases["agent_run_happy"] = {
            "exit": 0,
            "stdout_format": "json-object",
            "stderr_empty": True,
            "product_keys": sorted(live),
            "run_keys": sorted(run),
            "event_keys": sorted(EXPECTED_EVENT_KEYS),
            "event_count": len(events),
            "event_types": [item["event_type"] for item in events],
            "tool_trace_actions": [item.get("action") for item in trace],
            "request_rounds": len(requests),
            "request_keys": sorted(EXPECTED_REQUEST_KEYS),
            "first_message_roles": first_roles,
            "second_message_roles": second_roles,
            "tools_present": False,
            "tool_choice_present": False,
            "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
            "content_type": requests[0].get("content_type"),
            "authorization_present": requests[0].get("authorization") is not None,
            "user_agent": requests[0].get("user_agent"),
            "durable_receipt_count": len(artifacts),
        }

        replay_project = root / "replay-project"
        replay_state = root / "replay-state"
        prepare_project(replay_project)
        replay_result = run_python(
            repo,
            replay_project,
            replay_state,
            [
                "agent",
                "replay",
                "change VALUE to 2",
                json.dumps([{"patch": PATCH, "rationale": "fixture"}]),
                json.dumps(VERIFIER),
                "--mode",
                "safe-autonomous",
                "--authorized",
                "--attempts",
                "1",
                "--timeout",
                "5",
            ],
        )
        if replay_result["exit"] != 0 or replay_result["stderr"]:
            raise AssertionError(f"agent replay happy path failed: {replay_result}")
        replay = replay_result.get("value")
        if not isinstance(replay, dict) or replay.get("ok") is not True or replay.get("surface") != "agent-replay":
            raise AssertionError(f"agent replay output contract drift: {replay_result}")
        cases["agent_replay_happy"] = {
            "exit": 0,
            "stdout_format": "json-object",
            "stderr_empty": True,
            "surface": "agent-replay",
            "keys": sorted(replay),
        }

        missing = run_python(
            repo,
            replay_project,
            root / "missing-state",
            ["agent", "replay", "change VALUE to 2"],
        )
        cases["replay_missing_required_arguments"] = _assert_argparse_failure("replay missing required arguments", missing)

        malformed_replay = run_python(
            repo,
            replay_project,
            root / "malformed-replay-state",
            ["agent", "replay", "change VALUE to 2", "{", json.dumps(VERIFIER)],
        )
        cases["replay_malformed_json"] = _assert_public_failure("replay malformed JSON", malformed_replay)

        def model_failure_case(label: str, endpoint: MockEndpoint, *, timeout: str = "5") -> dict[str, Any]:
            project = root / f"{label}-project"
            state = root / f"{label}-state"
            prepare_project(project)
            with endpoint as active:
                result = run_python(repo, project, state, _agent_run_args(active.endpoint, timeout=timeout))
            return _assert_public_failure(label, result)

        cases["model_response_not_json_action"] = model_failure_case(
            "model-not-json",
            MockEndpoint(["not a JSON action"]),
        )
        cases["model_unknown_action"] = model_failure_case(
            "model-unknown-action",
            MockEndpoint([json.dumps({"action": "explode"})]),
        )
        cases["model_malformed_action_arguments"] = model_failure_case(
            "model-malformed-arguments",
            MockEndpoint([json.dumps({"action": "inspect", "paths": "not-a-list"})]),
        )
        cases["model_empty_chat_output"] = model_failure_case(
            "model-empty-output",
            MockEndpoint([""]),
        )
        cases["model_http_500"] = model_failure_case(
            "model-http-500",
            MockEndpoint(status=500),
        )
        cases["model_invalid_http_json"] = model_failure_case(
            "model-invalid-http-json",
            MockEndpoint(raw_body=b"not-json"),
        )

        unavailable_project = root / "unavailable-project"
        unavailable_state = root / "unavailable-state"
        prepare_project(unavailable_project)
        unavailable = run_python(
            repo,
            unavailable_project,
            unavailable_state,
            _agent_run_args(_closed_local_endpoint(), timeout="0.2"),
        )
        cases["model_endpoint_unavailable"] = _assert_public_failure("model endpoint unavailable", unavailable)

        timeout_project = root / "timeout-project"
        timeout_state = root / "timeout-state"
        prepare_project(timeout_project)
        with MockEndpoint([patch_action], delay_seconds=0.30) as endpoint:
            timed_out = run_python(
                repo,
                timeout_project,
                timeout_state,
                _agent_run_args(endpoint.endpoint, timeout="0.05"),
            )
        cases["model_endpoint_timeout"] = _assert_public_failure("model endpoint timeout", timed_out)

    return {
        "ok": True,
        "schema_version": 1,
        "family": "agent",
        "engine": "python",
        "routes": ["agent run", "agent replay"],
        "transport_protocol": "openai-compatible-chat-json-action",
        "api_tool_calling": {
            "tools_present": False,
            "tool_choice_present": False,
            "tool_result_transport": "assistant-action-then-user-tool-result-message",
        },
        "exit_policy": {
            "success": 0,
            "application_or_gateway_error": 4,
            "argument_parser_error": 2,
        },
        "nondeterministic_fields": [
            "timestamps",
            "execution durations",
            "cryptographic run/receipt identifiers",
            "temporary workspace paths",
        ],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Python-only agent public reference contract")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:  # The artifact must explain a red gate, not merely disappear.
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "agent",
            "engine": "python",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
