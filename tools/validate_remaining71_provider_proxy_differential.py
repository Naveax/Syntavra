#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


STREAM_BODY = b'data: {"delta":"one"}\n\ndata: [DONE]\n\n'
SECRET_STREAM_BODY = b'data: {"delta":"api_key=super-secret-value"}\n\ndata: [DONE]\n\n'
CONTROL_TOKEN = "c" * 32
PROVIDER_KEY = "server-secret"
CLIENT_KEY = "client-secret"


class UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.last_authorization = ""
        self.last_payload: dict[str, Any] = {}
        self.stream_body = STREAM_BODY

    def reset(self) -> None:
        with self.lock:
            self.calls = 0
            self.last_authorization = ""
            self.last_payload = {}
            self.stream_body = STREAM_BODY

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "calls": self.calls,
                "last_authorization": self.last_authorization,
                "last_payload": dict(self.last_payload),
            }


UPSTREAM = UpstreamState()


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        with UPSTREAM.lock:
            UPSTREAM.calls += 1
            UPSTREAM.last_authorization = self.headers.get("Authorization", "")
            UPSTREAM.last_payload = payload
            stream_body = UPSTREAM.stream_body
        if payload.get("stream"):
            body = stream_body
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        body = json.dumps(
            {
                "id": "resp-proxy",
                "output_text": "answer",
                "usage": {
                    "input_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens": 3,
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


@dataclass
class ProxyProcess:
    process: subprocess.Popen[str]
    host: str
    port: int
    stdout_lines: list[str]
    stderr_lines: list[str]
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.stdout_thread.join(timeout=2)
        self.stderr_thread.join(timeout=2)


def _reader(stream: Any, sink: list[str], events: queue.Queue[str] | None = None) -> None:
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
            if events is not None:
                events.put(line)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _json_object_from_lines(events: queue.Queue[str], process: subprocess.Popen[str], timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    buffer = ""
    while time.monotonic() < deadline:
        if process.poll() is not None and events.empty():
            raise RuntimeError(f"proxy exited before readiness with code {process.returncode}")
        try:
            line = events.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        buffer += line
        stripped = buffer.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            if len(buffer) > 128 * 1024:
                raise RuntimeError("proxy readiness output exceeded 128 KiB")
            continue
        if isinstance(value, dict) and value.get("event") == "PROVIDER_PROXY_READY":
            return value
        buffer = ""
    raise TimeoutError("timed out waiting for PROVIDER_PROXY_READY")


def _launch_proxy(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
    upstream_port: int,
) -> ProxyProcess:
    common = [
        "--project",
        str(project),
        "--state-root",
        str(state_root),
        "provider",
        "proxy",
        "--provider",
        "openai",
        "--upstream",
        f"http://127.0.0.1:{upstream_port}",
        "--credential-env",
        "TEST_PROVIDER_KEY",
        "--control-token-env",
        "TEST_SYNTAVRA_CONTROL_TOKEN",
        "--allow-insecure-upstream",
        "--listen-host",
        "127.0.0.1",
        "--listen-port",
        "0",
        "--timeout-seconds",
        "5",
    ]
    if engine == "python":
        command = [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", *common]
    elif engine == "rust":
        command = [str(rust_bin), "--engine", "rust", *common]
    else:
        raise ValueError(engine)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "TEST_PROVIDER_KEY": PROVIDER_KEY,
            "TEST_SYNTAVRA_CONTROL_TOKEN": CONTROL_TOKEN,
            "SYNTAVRA_BULK_PARITY_PROBE": "1",
        }
    )
    process = subprocess.Popen(
        command,
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    events: queue.Queue[str] = queue.Queue()
    stdout_thread = threading.Thread(target=_reader, args=(process.stdout, stdout_lines, events), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(process.stderr, stderr_lines), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        ready = _json_object_from_lines(events, process, 20.0)
        listen = ready.get("listen") or {}
        host = str(listen.get("host") or "127.0.0.1")
        port = int(listen["port"])
        return ProxyProcess(process, host, port, stdout_lines, stderr_lines, stdout_thread, stderr_thread)
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        raise RuntimeError(
            f"{engine} proxy launch failed\nstdout:\n{''.join(stdout_lines)}\nstderr:\n{''.join(stderr_lines)}"
        )


def _request(
    proxy: ProxyProcess,
    payload: dict[str, Any],
    *,
    authorization: str = f"Bearer {CLIENT_KEY}",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(proxy.host, proxy.port, timeout=8)
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection.request(
        "POST",
        "/v1/responses",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Authorization": authorization,
        },
    )
    response = connection.getresponse()
    raw = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, raw


def _control(proxy: ProxyProcess, path: str, *, token: str = "") -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection(proxy.host, proxy.port, timeout=8)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw)


def _absolute_target(proxy: ProxyProcess, payload: dict[str, Any]) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(proxy.host, proxy.port, timeout=8)
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection.putrequest("POST", "http://attacker.invalid/v1/responses", skip_host=True)
    connection.putheader("Host", "attacker.invalid")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, raw


def _payload(*, stream: bool = False) -> dict[str, Any]:
    return {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "question"},
        ],
        "temperature": 0,
        "stream": stream,
    }


def _evidence_header(headers: dict[str, str]) -> bool:
    return headers.get("x-syntavra-evidence", "").startswith("sc://sha256/")


def exercise(engine: str, *, repo: Path, rust_bin: Path, upstream_port: int, root: Path) -> dict[str, Any]:
    UPSTREAM.reset()
    project = root / engine / "project"
    state_root = root / engine / "state"
    project.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)
    proxy = _launch_proxy(
        engine,
        repo=repo,
        rust_bin=rust_bin,
        project=project,
        state_root=state_root,
        upstream_port=upstream_port,
    )
    try:
        normal_status, normal_headers, normal_raw = _request(proxy, _payload())
        normal_json = json.loads(normal_raw)
        upstream_after_first = UPSTREAM.snapshot()
        replay_status, replay_headers, replay_raw = _request(proxy, _payload())
        replay_json = json.loads(replay_raw)
        upstream_after_replay = UPSTREAM.snapshot()

        unauth_status, unauth_json = _control(proxy, "/_syntavra/health")
        health_status, health_json = _control(proxy, "/_syntavra/health", token=CONTROL_TOKEN)
        ready_status, ready_json = _control(proxy, "/_syntavra/ready", token=CONTROL_TOKEN)

        before_stream_calls = UPSTREAM.snapshot()["calls"]
        stream_status, stream_headers, stream_raw = _request(proxy, _payload(stream=True))
        second_stream_status, _, second_stream_raw = _request(proxy, _payload(stream=True))
        after_stream_calls = UPSTREAM.snapshot()["calls"]

        with UPSTREAM.lock:
            UPSTREAM.stream_body = SECRET_STREAM_BODY
        dlp_status, dlp_headers, dlp_raw = _request(proxy, _payload(stream=True))
        dlp_json = json.loads(dlp_raw)

        before_absolute_calls = UPSTREAM.snapshot()["calls"]
        absolute_status, absolute_raw = _absolute_target(proxy, _payload())
        after_absolute_calls = UPSTREAM.snapshot()["calls"]

        return {
            "normal": {
                "status": normal_status,
                "answer": normal_json.get("output_text"),
                "replay": normal_headers.get("x-syntavra-replay"),
                "evidence": _evidence_header(normal_headers),
                "upstream_auth_is_server": upstream_after_first["last_authorization"] == f"Bearer {PROVIDER_KEY}",
                "client_auth_not_forwarded": upstream_after_first["last_authorization"] != f"Bearer {CLIENT_KEY}",
                "prompt_cache_key_present": "prompt_cache_key" in upstream_after_first["last_payload"],
            },
            "replay": {
                "status": replay_status,
                "body_id": replay_json.get("id"),
                "replay": replay_headers.get("x-syntavra-replay"),
                "evidence": _evidence_header(replay_headers),
                "upstream_calls": upstream_after_replay["calls"],
            },
            "control": {
                "unauth_status": unauth_status,
                "unauth_error": unauth_json.get("error"),
                "health_status": health_status,
                "health_ok": bool(health_json.get("ok")),
                "ready_status": ready_status,
                "ready": bool(ready_json.get("ready")),
            },
            "stream": {
                "status": stream_status,
                "exact": stream_raw == STREAM_BODY,
                "capture": stream_headers.get("x-syntavra-capture"),
                "evidence": _evidence_header(stream_headers),
                "second_status": second_stream_status,
                "second_exact": second_stream_raw == STREAM_BODY,
                "upstream_call_delta": after_stream_calls - before_stream_calls,
            },
            "dlp": {
                "status": dlp_status,
                "error": dlp_json.get("error"),
                "secret_absent": b"super-secret-value" not in dlp_raw,
                "evidence": str(dlp_json.get("evidence_handle") or dlp_headers.get("x-syntavra-evidence") or "").startswith("sc://sha256/"),
            },
            "fixed_origin": {
                "absolute_status": absolute_status,
                "upstream_call_delta": after_absolute_calls - before_absolute_calls,
                "secret_absent": b"super-secret-value" not in absolute_raw,
            },
        }
    finally:
        proxy.close()


def compare(python_result: dict[str, Any], rust_result: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "normal": {
            "status": 200,
            "answer": "answer",
            "replay": "miss",
            "evidence": True,
            "upstream_auth_is_server": True,
            "client_auth_not_forwarded": True,
            "prompt_cache_key_present": True,
        },
        "replay": {
            "status": 200,
            "body_id": "resp-proxy",
            "replay": "hit",
            "evidence": False,
            "upstream_calls": 1,
        },
        "control": {
            "unauth_status": 401,
            "unauth_error": "invalid-control-token",
            "health_status": 200,
            "health_ok": True,
            "ready_status": 200,
            "ready": True,
        },
        "stream": {
            "status": 200,
            "exact": True,
            "capture": "complete-before-delivery",
            "evidence": True,
            "second_status": 200,
            "second_exact": True,
            "upstream_call_delta": 2,
        },
        "dlp": {
            "status": 502,
            "error": "stream-dlp-blocked",
            "secret_absent": True,
            "evidence": True,
        },
        "fixed_origin": {
            "absolute_status": 502,
            "upstream_call_delta": 0,
            "secret_absent": True,
        },
    }
    mismatches: list[dict[str, Any]] = []
    for section, fields in expected.items():
        for field, expected_value in fields.items():
            py_value = python_result.get(section, {}).get(field)
            rs_value = rust_result.get(section, {}).get(field)
            if py_value != expected_value or rs_value != expected_value or py_value != rs_value:
                mismatches.append(
                    {
                        "path": f"{section}.{field}",
                        "expected": expected_value,
                        "python": py_value,
                        "rust": rs_value,
                    }
                )
    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "claim_boundary": "local deterministic transport differential only; no external provider or billing claim",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust provider-proxy behavioral parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)
    if not rust_bin.is_file():
        raise SystemExit(f"Rust selector binary not found: {rust_bin}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-provider-proxy-diff-") as directory:
            root = Path(directory)
            python_result = exercise(
                "python",
                repo=repo,
                rust_bin=rust_bin,
                upstream_port=server.server_address[1],
                root=root,
            )
            rust_result = exercise(
                "rust",
                repo=repo,
                rust_bin=rust_bin,
                upstream_port=server.server_address[1],
                root=root,
            )
            differential = compare(python_result, rust_result)
            result = {
                "ok": differential["ok"],
                "python": python_result,
                "rust": rust_result,
                "differential": differential,
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
