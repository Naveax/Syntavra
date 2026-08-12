#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from syntavra_runtime import unified_cli
from syntavra_runtime.agent_runtime import AgentDeliveryMode, AgentRuntime
from syntavra_runtime.autonomous_agent import AgentMode, AutonomousCodingAgent
from syntavra_runtime.model_gateway import GatewayError, SequenceModelGateway


SCHEMA_VERSION = 1


class _Graph:
    def stats(self) -> dict[str, int]:
        return {"files": 1, "nodes": 1, "edges": 0}

    def index_repository(self, project: Path) -> dict[str, int]:
        del project
        return self.stats()

    def query(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [{"node_id": "fixture:value", "name": "VALUE", "path": "module.py", "query": query, "limit": limit}]

    def impact(self, node_id: str, *, max_depth: int = 6) -> dict[str, Any]:
        return {"root": node_id, "impacted": [], "max_depth": max_depth}


class _MockState:
    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    server_version = "SyntavraAgentReference/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format, args

    def do_POST(self) -> None:  # noqa: N802
        state: _MockState = self.server.mock_state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_invalid_json": raw.decode("utf-8", errors="replace")}
        state.requests.append(
            {
                "path": self.path,
                "method": "POST",
                "content_type": self.headers.get("Content-Type", ""),
                "authorization": self.headers.get("Authorization", ""),
                "payload": payload,
            }
        )
        if state.status != 200:
            body = json.dumps({"error": "fixture-http-error"}).encode("utf-8")
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        action = {
            "action": "edit",
            "rationale": "reference fixture update",
            "edits": [
                {
                    "path": "module.py",
                    "operation": "replace",
                    "old": "VALUE = 1",
                    "new": "VALUE = 2",
                    "count": 1,
                }
            ],
        }
        body = json.dumps(
            {
                "id": "fixture-response",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(action, sort_keys=True)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def _mock_gateway(*, status: int = 200):
    state = _MockState(status=status)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.mock_state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)


def _fixture_project(root: Path, *, with_verifier: bool = True) -> Path:
    project = root / "project"
    project.mkdir(parents=True)
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    if with_verifier:
        (project / "Makefile").write_text(
            "test:\n\tpython3 -c \"from pathlib import Path; assert 'VALUE = 2' in Path('module.py').read_text()\"\n",
            encoding="utf-8",
        )
    init = _run(["git", "init"], cwd=project)
    if init.returncode != 0:
        raise RuntimeError(f"git init failed: {init.stderr}")
    for key, value in (("user.email", "syntavra-fixture@example.invalid"), ("user.name", "Syntavra Fixture")):
        configured = _run(["git", "config", key, value], cwd=project)
        if configured.returncode != 0:
            raise RuntimeError(f"git config failed: {configured.stderr}")
    added = _run(["git", "add", "-A"], cwd=project)
    committed = _run(["git", "commit", "-m", "fixture"], cwd=project)
    if added.returncode != 0 or committed.returncode != 0:
        raise RuntimeError(f"fixture commit failed: {added.stderr}\n{committed.stderr}")
    return project


def _platform(project: Path, state: Path) -> SimpleNamespace:
    graph = _Graph()
    agent_runtime = AgentRuntime(
        project=project,
        state_root=state / "agent-product",
        graph=graph,
        memory=None,
        sandbox=None,
    )
    autonomous_agent = AutonomousCodingAgent(
        project,
        state / "agent-replay",
        graph=graph,
        memory=None,
        sandbox=None,
    )
    return SimpleNamespace(agent_runtime=agent_runtime, autonomous_agent=autonomous_agent)


def _normalize_run_output(value: dict[str, Any]) -> dict[str, Any]:
    run = value.get("run") if isinstance(value.get("run"), dict) else {}
    return {
        "ok": value.get("ok"),
        "provider": value.get("provider"),
        "model": value.get("model"),
        "verification_complete": value.get("verification_complete"),
        "delivery_mode": (value.get("delivery") or {}).get("mode") if isinstance(value.get("delivery"), dict) else None,
        "delivery_ok": (value.get("delivery") or {}).get("ok") if isinstance(value.get("delivery"), dict) else None,
        "run_state": run.get("state"),
        "stop_reason": run.get("stop_reason"),
        "changed_files": run.get("changed_files"),
        "attempt_count": len(run.get("attempts") or []),
        "tool_actions": [item.get("action") for item in value.get("tool_trace", []) if isinstance(item, dict)],
        "event_types": [item.get("event_type") for item in value.get("events", []) if isinstance(item, dict)],
        "usage": value.get("usage"),
    }


def validate() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-python-agent-reference-") as raw_root:
        root = Path(raw_root)
        project = _fixture_project(root)
        state = root / "state"
        platform = _platform(project, state)
        events_path = root / "agent-events.jsonl"

        with _mock_gateway() as (endpoint, mock):
            stdout = io.StringIO()
            with patch.object(unified_cli, "SyntavraPlatform", side_effect=lambda *_args, **_kwargs: platform):
                with contextlib.redirect_stdout(stdout):
                    run_exit = unified_cli._core_main(
                        [
                            "--project", str(project),
                            "--state-root", str(state),
                            "agent", "run", "change VALUE to two",
                            "--provider", "openai-compatible",
                            "--model", "fixture-model",
                            "--endpoint", endpoint,
                            "--api-mode", "chat",
                            "--mode", AgentMode.SAFE_AUTONOMOUS.value,
                            "--delivery", AgentDeliveryMode.DIFF.value,
                            "--events-jsonl", str(events_path),
                        ]
                    )
            run_output = json.loads(stdout.getvalue())
            request = mock.requests[0] if mock.requests else {}

        event_rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        receipt_paths = sorted((state / "agent-product" / "agent-receipts").glob("*.json"))
        durable = json.loads(receipt_paths[0].read_text(encoding="utf-8")) if len(receipt_paths) == 1 else {}

        patch_text = str((run_output.get("run") or {}).get("final_diff") or "")
        replay_rows = [{"patch": patch_text, "rationale": "reference fixture replay"}]
        verifier = ["make", "test"]
        replay_stdout = io.StringIO()
        with patch.object(unified_cli, "SyntavraPlatform", side_effect=lambda *_args, **_kwargs: platform):
            with contextlib.redirect_stdout(replay_stdout):
                replay_exit = unified_cli._core_main(
                    [
                        "--project", str(project),
                        "--state-root", str(state),
                        "agent", "replay", "change VALUE to two",
                        json.dumps(replay_rows),
                        json.dumps(verifier),
                        "--mode", AgentMode.SAFE_AUTONOMOUS.value,
                        "--authorized",
                    ]
                )
        replay_output = json.loads(replay_stdout.getvalue())

        malformed_replay_error = ""
        with patch.object(unified_cli, "SyntavraPlatform", side_effect=lambda *_args, **_kwargs: platform):
            try:
                unified_cli._core_main(
                    [
                        "--project", str(project),
                        "--state-root", str(state),
                        "agent", "replay", "bad replay", "{}", json.dumps(verifier),
                    ]
                )
            except ValueError as error:
                malformed_replay_error = str(error)

        malformed_model_error = ""
        try:
            platform.agent_runtime.run(
                "malformed model response",
                SequenceModelGateway(["not-json"]),
                mode=AgentMode.SAFE_AUTONOMOUS,
                delivery_mode=AgentDeliveryMode.DIFF,
            )
        except ValueError as error:
            malformed_model_error = str(error)

        missing_verifier_root = root / "missing-verifier"
        missing_project = _fixture_project(missing_verifier_root, with_verifier=False)
        missing_runtime = AgentRuntime(
            project=missing_project,
            state_root=missing_verifier_root / "state",
            graph=_Graph(),
            memory=None,
            sandbox=None,
        )
        missing_verifier_error = ""
        try:
            missing_runtime.run(
                "no verifier",
                SequenceModelGateway([{"action": "diff"}]),
                mode=AgentMode.SAFE_AUTONOMOUS,
            )
        except RuntimeError as error:
            missing_verifier_error = str(error)

        http_error = ""
        with _mock_gateway(status=500) as (endpoint, _mock):
            error_platform = _platform(project, root / "http-error-state")
            with patch.object(unified_cli, "SyntavraPlatform", side_effect=lambda *_args, **_kwargs: error_platform):
                try:
                    unified_cli._core_main(
                        [
                            "--project", str(project),
                            "--state-root", str(root / "http-error-state"),
                            "agent", "run", "http failure fixture",
                            "--provider", "openai-compatible",
                            "--model", "fixture-model",
                            "--endpoint", endpoint,
                            "--api-mode", "chat",
                            "--mode", AgentMode.SAFE_AUTONOMOUS.value,
                        ]
                    )
                except GatewayError as error:
                    http_error = str(error)

        normalized = _normalize_run_output(run_output)
        expected_events = [
            "agent-started",
            "verification-plan",
            "patch-proposed",
            "primary-run-finished",
            "delivery-finished",
        ]
        request_payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
        request_messages = request_payload.get("messages") if isinstance(request_payload, dict) else []
        request_roles = [item.get("role") for item in request_messages if isinstance(item, dict)]

        checks = {
            "run_exit_zero": run_exit == 0,
            "run_ok": normalized["ok"] is True,
            "run_completed": normalized["run_state"] == "completed" and normalized["stop_reason"] == "verifier passed",
            "run_changed_expected_file": normalized["changed_files"] == ["module.py"],
            "run_single_attempt": normalized["attempt_count"] == 1,
            "run_tool_trace": normalized["tool_actions"] == ["edit"],
            "run_delivery": normalized["delivery_mode"] == "diff" and normalized["delivery_ok"] is True,
            "run_usage": normalized["usage"] == {"input_tokens": 7, "output_tokens": 5, "total_tokens": 12},
            "request_path": request.get("path") == "/chat/completions",
            "request_content_type": str(request.get("content_type") or "").startswith("application/json"),
            "request_model": request_payload.get("model") == "fixture-model",
            "request_roles": request_roles[:2] == ["system", "user"],
            "request_no_auth_without_key": request.get("authorization") == "",
            "events_contiguous": [row.get("sequence") for row in event_rows] == list(range(1, len(event_rows) + 1)),
            "events_expected": normalized["event_types"] == expected_events and [row.get("event_type") for row in event_rows] == expected_events,
            "durable_receipt_one": len(receipt_paths) == 1,
            "durable_receipt_state": durable.get("state") == "completed" and durable.get("stop_reason") == "verifier passed",
            "durable_receipt_changed_files": durable.get("changed_files") == ["module.py"],
            "replay_exit_zero": replay_exit == 0,
            "replay_ok": replay_output.get("ok") is True,
            "replay_surface": replay_output.get("surface") == "agent-replay",
            "replay_completed": replay_output.get("state") == "completed" and replay_output.get("stop_reason") == "verifier passed",
            "replay_changed_expected_file": replay_output.get("changed_files") == ["module.py"],
            "malformed_replay_rejected": malformed_replay_error == "agent replay requires a patch list and non-empty verifier argv",
            "malformed_model_rejected": malformed_model_error == "model response is not a JSON action",
            "missing_verifier_rejected": missing_verifier_error == "agent cannot run safely because no project verifier was discovered",
            "http_error_rejected": "model endpoint returned HTTP 500" in http_error,
        }

        return {
            "ok": all(checks.values()),
            "schema_version": SCHEMA_VERSION,
            "surface": ["agent run", "agent replay"],
            "network_boundary": "localhost-only deterministic OpenAI-compatible fixture",
            "run": normalized,
            "request": {
                "path": request.get("path"),
                "method": request.get("method"),
                "content_type": request.get("content_type"),
                "authorization_present": bool(request.get("authorization")),
                "model": request_payload.get("model"),
                "roles": request_roles,
            },
            "events": {
                "count": len(event_rows),
                "types": [row.get("event_type") for row in event_rows],
                "contiguous": checks["events_contiguous"],
            },
            "durable_receipt": {
                "count": len(receipt_paths),
                "state": durable.get("state"),
                "stop_reason": durable.get("stop_reason"),
                "changed_files": durable.get("changed_files"),
            },
            "replay": {
                "exit": replay_exit,
                "ok": replay_output.get("ok"),
                "surface": replay_output.get("surface"),
                "state": replay_output.get("state"),
                "stop_reason": replay_output.get("stop_reason"),
                "changed_files": replay_output.get("changed_files"),
            },
            "negative": {
                "malformed_replay": malformed_replay_error,
                "malformed_model": malformed_model_error,
                "missing_verifier": missing_verifier_error,
                "http_error": http_error,
            },
            "checks": checks,
            "failed_checks": sorted(name for name, passed in checks.items() if not passed),
        }


def main() -> int:
    value = validate()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
