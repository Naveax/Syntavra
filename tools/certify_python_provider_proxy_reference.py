#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.provider_proxy import ProviderProxyRuntime, ProxyConfig
from syntavra_runtime.proxy_product import ProxyProductRegistry
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


FIXTURE_RELATIVE = Path("contracts/python/provider-proxy-reference-v1.json")
CONTROL_TOKEN = "c" * 32
PROVIDER_KEY = "server-secret"
CLIENT_KEY = "client-secret"


def _head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    result = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python",
         "--project", str(project), "--state-root", str(state), *args],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
    )
    try:
        value = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "value": value}


def _json_result(label: str, result: dict[str, Any], exit_code: int = 0) -> Any:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected exit={exit_code} and empty stderr, got {result}")
    if result["value"] is None:
        raise AssertionError(f"{label}: expected JSON stdout, got {result}")
    return result["value"]


def _family_routes(fixture: dict[str, Any]) -> dict[str, Any]:
    routes = public_surface.python_public_route_sources()
    derived = sorted(
        route for route in routes
        if route.startswith("provider ")
        or route in {"run gateway-plan", "run provider-pool", "run provider-route", "run proxy-plan"}
        or route.startswith("run proxy-service ")
    )
    expected = fixture["public_routes"]
    if derived != expected:
        raise AssertionError(f"provider/proxy public route inventory drift: {derived}")
    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    ownership = {}
    for route in derived:
        row = execution[route]
        if len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"provider/proxy route ownership drift: {row}")
        ownership[route] = row["entrypoint"]
    return {
        "routes": derived,
        "route_count": len(derived),
        "route_sha256": public_surface._digest(derived),
        "ownership": ownership,
    }


def _failure_envelope(label: str, result: dict[str, Any], *, contains: str) -> dict[str, Any]:
    value = _json_result(label, result, 4)
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: public failure envelope drift: {value}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error code drift: {value}")
    details = error.get("details")
    if not isinstance(details, dict) or contains not in str(details.get("error") or ""):
        raise AssertionError(f"{label}: expected {contains!r} in failure details: {value}")
    return {
        "exit": 4,
        "code": error["code"],
        "fallback": details.get("fallback"),
        "error_type": str(details.get("error") or "").split(":", 1)[0],
    }


def _provider_gateway_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    capabilities = _json_result("provider capabilities", _run(repo, project, state, ["provider", "capabilities"]))
    providers = sorted(capabilities)
    if providers != fixture["provider_gateway"]["canonical_providers"]:
        raise AssertionError(f"provider capability inventory drift: {providers}")

    azure = _json_result("azure alias", _run(repo, project, state, ["provider", "capabilities", "azure-openai"]))
    if azure.get("provider") != fixture["provider_gateway"]["azure_openai_canonical"]:
        raise AssertionError(f"azure-openai alias drift: {azure}")
    unsupported = _failure_envelope(
        "unsupported provider",
        _run(repo, project, state, ["provider", "capabilities", "not-a-provider"]),
        contains="unsupported provider",
    )

    request = {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "stable fixture"},
            {"role": "user", "content": "question"},
        ],
        "temperature": 0,
        "stream": False,
    }
    request_text = json.dumps(request, separators=(",", ":"))
    plan = _json_result(
        "provider prepare",
        _run(repo, project, state, ["provider", "prepare", "openai", "--request", request_text, "--cache-policy", "read-write"]),
    )
    expected_plan_keys = sorted(field.name for field in dataclasses.fields(__import__("syntavra_runtime.provider_gateway", fromlist=["ProviderPlan"]).ProviderPlan))
    if sorted(plan) != expected_plan_keys:
        raise AssertionError(f"provider plan schema drift: {sorted(plan)}")
    if plan.get("provider") != "openai" or plan.get("model") != "gpt-test" or plan.get("replay_hit") is not False:
        raise AssertionError(f"provider plan semantics drift: {plan}")
    prepared = plan.get("prepared_request")
    if not isinstance(prepared, dict) or not prepared.get("prompt_cache_key"):
        raise AssertionError(f"OpenAI prompt cache preparation drift: {plan}")

    credential_request = json.dumps({**request, "authorization": "Bearer must-not-enter-payload"}, separators=(",", ":"))
    credential_rejection = _failure_envelope(
        "credential in request payload",
        _run(repo, project, state, ["provider", "prepare", "openai", "--request", credential_request]),
        contains="credential field is transport-only",
    )

    plan_path = project / "plan.json"
    response_path = project / "response.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    response = {
        "id": "resp-reference",
        "output_text": "answer",
        "usage": {"input_tokens": 12, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 3},
    }
    response_path.write_text(json.dumps(response), encoding="utf-8")
    capture = _json_result(
        "provider capture",
        _run(repo, project, state, ["provider", "capture", "--plan", str(plan_path), "--response", str(response_path)]),
    )
    if capture.get("provider") != "openai" or capture.get("replay_stored") is not True:
        raise AssertionError(f"provider capture drift: {capture}")
    replay = _json_result(
        "provider replay",
        _run(repo, project, state, ["provider", "replay", "--plan", str(plan_path)]),
    )
    if replay.get("id") != "resp-reference" or replay.get("output_text") != "answer":
        raise AssertionError(f"provider replay drift: {replay}")
    stats = _json_result("provider stats", _run(repo, project, state, ["provider", "stats"]))
    verify = _json_result("provider verify", _run(repo, project, state, ["provider", "verify"]))
    if verify.get("ok") is not True:
        raise AssertionError(f"provider verify drift: {verify}")

    dry = _json_result(
        "provider proxy dry-run",
        _run(repo, project, state, [
            "provider", "proxy", "--provider", "openai", "--upstream", "https://api.example.invalid",
            "--credential-env", "TEST_PROVIDER_KEY", "--control-token-env", "TEST_CONTROL_TOKEN", "--dry-run",
        ]),
    )
    config = dry.get("config")
    if dry.get("ok") is not True or not isinstance(config, dict):
        raise AssertionError(f"provider proxy dry-run drift: {dry}")
    if sorted(config) != fixture["proxy_config"]["fields"]:
        raise AssertionError(f"ProxyConfig field drift: {sorted(config)}")
    for key, expected in fixture["proxy_config"]["defaults"].items():
        if config.get(key) != expected:
            raise AssertionError(f"ProxyConfig default drift at {key}: {config.get(key)!r} != {expected!r}")

    return {
        "canonical_providers": providers,
        "capability_record_keys": sorted(next(iter(capabilities.values()))),
        "azure_openai_canonical": azure["provider"],
        "unsupported": unsupported,
        "credential_payload_rejection": credential_rejection,
        "plan_keys": sorted(plan),
        "prepared_request_has_prompt_cache_key": True,
        "capture_keys": sorted(capture),
        "capture_replay_stored": True,
        "replay_exact_fixture": replay == response,
        "stats_keys": sorted(stats),
        "verify_keys": sorted(verify),
        "proxy_config": config,
    }


def _helper_contract(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    gateway = _json_result(
        "gateway plan",
        _run(repo, project, state, ["run", "gateway-plan", "openai", "--upstream", "https://api.example.invalid"]),
    )
    if gateway.get("ok") is not True or gateway.get("provider") != "openai" or gateway.get("agent_environment_contains_secret") is not False:
        raise AssertionError(f"secretless gateway plan drift: {gateway}")

    proxy_plan = _json_result("proxy plan", _run(repo, project, state, ["run", "proxy-plan", "openai"]))
    if proxy_plan.get("ok") is not True or proxy_plan.get("stream_mode") != "commit-before-forward" or proxy_plan.get("credential_policy") != "transport-only":
        raise AssertionError(f"proxy plan drift: {proxy_plan}")
    presets = sorted(row["provider"] for row in ProxyProductRegistry.records())
    if presets != fixture["proxy_presets"]:
        raise AssertionError(f"proxy preset inventory drift: {presets}")

    home = project / "fixture-home"
    home.mkdir()
    service = _json_result(
        "proxy service plan",
        _run(repo, project, state, ["run", "proxy-service", "plan", "openai", "--platform", "linux", "--home", str(home)]),
    )
    if service.get("ok") is not True or service.get("action") != "plan":
        raise AssertionError(f"proxy service plan drift: {service}")
    spec = service.get("spec")
    if not isinstance(spec, dict) or not any(str(item) == "proxy" for item in spec.get("command", [])):
        raise AssertionError(f"proxy service spec drift: {service}")

    candidates = [
        {
            "provider": "openai", "model": "free-model", "quality": 0.9,
            "quota_remaining": 1.0, "latency_ms": 20.0, "max_complexity": "reasoning",
        },
        {
            "provider": "anthropic", "model": "subscription-model", "quality": 0.8,
            "quota_remaining": 1.0, "latency_ms": 20.0, "max_complexity": "reasoning",
            "subscription": True,
        },
    ]
    route = _json_result(
        "adaptive provider route",
        _run(repo, project, state, [
            "run", "provider-route", "security architecture root cause",
            json.dumps(candidates, separators=(",", ":")), "--changed-files", "10", "--tokens", "20000",
        ]),
    )
    if route.get("provider") != "anthropic" or route.get("model") != "subscription-model" or route.get("complexity") != "reasoning":
        raise AssertionError(f"adaptive provider route drift: {route}")

    primary = _json_result(
        "provider pool add primary",
        _run(repo, project, state, [
            "run", "provider-pool", "add", "openai", "primary", "env:OPENAI_API_KEY",
            "--subscription", "--priority", "10", "--model", "gpt-test",
        ]),
    )
    _json_result(
        "provider pool add backup",
        _run(repo, project, state, [
            "run", "provider-pool", "add", "openai", "backup", "env:OPENAI_BACKUP_KEY",
            "--priority", "0", "--model", "gpt-test",
        ]),
    )
    if primary.get("account") != "primary" or primary.get("credential_ref") != "env:OPENAI_API_KEY":
        raise AssertionError(f"provider pool registration drift: {primary}")
    pool_list = _json_result("provider pool list", _run(repo, project, state, ["run", "provider-pool", "list"]))
    accounts = pool_list.get("accounts") if isinstance(pool_list, dict) else None
    if not isinstance(accounts, list) or [row["account"] for row in accounts] != ["backup", "primary"]:
        # receipt order is provider then priority DESC, so for equal provider primary should actually precede backup.
        if not isinstance(accounts, list) or [row["account"] for row in accounts] != ["primary", "backup"]:
            raise AssertionError(f"provider pool list ordering drift: {pool_list}")
    models = [{"provider": "openai", "model": "gpt-test", "quality": 0.8, "max_complexity": "reasoning", "context_window": 128000}]
    pool_route = _json_result(
        "provider pool route",
        _run(repo, project, state, [
            "run", "provider-pool", "route", "openai", "unused", json.dumps(models, separators=(",", ":")), "--tokens", "1000",
        ]),
    )
    if pool_route.get("account") != "primary" or pool_route.get("provider") != "openai":
        raise AssertionError(f"provider pool routing drift: {pool_route}")

    raw_secret = _failure_envelope(
        "provider pool raw secret rejection",
        _run(repo, project, state, ["run", "provider-pool", "add", "openai", "leaky", "sk-" + "abcdefghijklmnop1234"]),
        contains="credential_ref must be a non-secret",
    )

    return {
        "gateway_plan": {
            "keys": sorted(gateway), "provider": gateway["provider"], "protocol": gateway["protocol"],
            "agent_environment_contains_secret": gateway["agent_environment_contains_secret"],
            "transport_visibility": gateway["transport_injection"]["visibility"],
        },
        "proxy_plan": {
            "keys": sorted(proxy_plan), "provider": proxy_plan["provider"]["provider"],
            "resolved_upstream": proxy_plan["resolved_upstream"], "stream_mode": proxy_plan["stream_mode"],
            "credential_policy": proxy_plan["credential_policy"],
        },
        "proxy_presets": presets,
        "proxy_service": {"keys": sorted(service), "spec_keys": sorted(spec), "action": service["action"]},
        "adaptive_route": {key: route[key] for key in ("provider", "model", "account", "complexity", "score", "reasons", "fallbacks", "receipt_hash")},
        "provider_pool": {
            "selected_account": pool_route["account"],
            "receipt_account_count": len(accounts),
            "raw_secret_rejection": raw_secret,
        },
    }


class _UpstreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.mode = "normal"
        self.last_headers: dict[str, str] = {}
        self.last_payload: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"calls": self.calls, "mode": self.mode, "last_headers": dict(self.last_headers), "last_payload": dict(self.last_payload)}


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _UpstreamState

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        with self.state.lock:
            self.state.calls += 1
            self.state.last_headers = {str(k).casefold(): str(v) for k, v in self.headers.items()}
            self.state.last_payload = payload
            mode = self.state.mode
        if mode == "http-429":
            body = json.dumps({"error": {"message": "rate limited fixture"}}, separators=(",", ":")).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "7")
            self.send_header("Authorization", "must-not-forward")
            self.send_header("X-Upstream-Fixture", "rate-limit")
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({
            "id": "resp-proxy-reference", "output_text": "answer",
            "usage": {"input_tokens": 12, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 3},
        }, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Authorization", "must-not-forward")
        self.send_header("X-Upstream-Fixture", "normal")
        self.end_headers()
        self.wfile.write(body)


def _proxy_request(host: str, port: int, payload: dict[str, Any], *, request_id: str = "attacker-request-id") -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    body = json.dumps(payload, separators=(",", ":")).encode()
    connection.request("POST", "/v1/responses", body=body, headers={
        "Content-Type": "application/json", "Content-Length": str(len(body)),
        "Authorization": f"Bearer {CLIENT_KEY}", "X-Request-ID": request_id,
    })
    response = connection.getresponse()
    raw = response.read()
    headers = {str(k).casefold(): str(v) for k, v in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, raw


def _control(host: str, port: int, path: str, token: str = "") -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw)


def _proxy_transport_contract(root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    state = _UpstreamState()
    handler = type("ReferenceUpstreamHandler", (_UpstreamHandler,), {"state": state})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    evidence = EvidenceStore(root / "proxy-evidence", project_id="python-provider-proxy-reference")
    ledger = UsageReceiptLedger(root / "proxy-usage.sqlite3", signing_key=b"provider-proxy-reference-key")
    gateway = ProviderGateway(root / "proxy-gateway.sqlite3", evidence=evidence, usage_ledger=ledger)
    config = ProxyConfig(
        provider="openai",
        upstream_base=f"http://127.0.0.1:{upstream.server_address[1]}",
        listen_port=0,
        credential_env="TEST_PROVIDER_KEY",
        control_token_env="TEST_CONTROL_TOKEN",
        allow_insecure_upstream=True,
        timeout_seconds=5,
        max_buffered_response_bytes=1024 * 1024,
    )
    proxy = ProviderProxyRuntime(config, gateway=gateway, insight_path=root / "proxy-insights.sqlite3")
    transport = fixture["transport"]
    try:
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": PROVIDER_KEY, "TEST_CONTROL_TOKEN": CONTROL_TOKEN}, clear=False):
            host, port = proxy.start()

            normal_payload = {
                "model": "gpt-normal", "messages": [{"role": "user", "content": "normal"}],
                "temperature": 0, "stream": False,
            }
            before = state.snapshot()["calls"]
            normal_status, normal_headers, normal_raw = _proxy_request(host, port, normal_payload)
            after_normal = state.snapshot()
            if normal_status != 200 or json.loads(normal_raw).get("output_text") != "answer":
                raise AssertionError(f"normal proxy response drift: {normal_status} {normal_headers} {normal_raw!r}")
            if after_normal["calls"] - before != 1:
                raise AssertionError(f"normal request retried unexpectedly: {after_normal}")
            if after_normal["last_headers"].get("authorization") != f"Bearer {PROVIDER_KEY}":
                raise AssertionError(f"provider credential injection drift: {after_normal}")
            if after_normal["last_headers"].get("x-request-id") == "attacker-request-id" or not after_normal["last_headers"].get("x-request-id", "").startswith("sc-"):
                raise AssertionError(f"request ID isolation drift: {after_normal}")
            if "prompt_cache_key" not in after_normal["last_payload"]:
                raise AssertionError(f"prepared upstream payload drift: {after_normal}")
            if "authorization" in normal_headers:
                raise AssertionError(f"upstream credential header leaked to client: {normal_headers}")
            if normal_headers.get("x-upstream-fixture") != "normal" or normal_headers.get("x-syntavra-replay") != "miss":
                raise AssertionError(f"proxy response header drift: {normal_headers}")
            if not normal_headers.get("x-syntavra-evidence", "").startswith("sc://sha256/"):
                raise AssertionError(f"proxy evidence header missing: {normal_headers}")

            replay_status, replay_headers, replay_raw = _proxy_request(host, port, normal_payload)
            after_replay = state.snapshot()
            if replay_status != 200 or json.loads(replay_raw).get("id") != "resp-proxy-reference" or replay_headers.get("x-syntavra-replay") != "hit":
                raise AssertionError(f"proxy replay drift: {replay_status} {replay_headers} {replay_raw!r}")
            if after_replay["calls"] != after_normal["calls"]:
                raise AssertionError(f"replay unexpectedly contacted upstream: {after_replay}")

            with state.lock:
                state.mode = "http-429"
            error_payload = {
                "model": "gpt-error", "messages": [{"role": "user", "content": "rate-limit"}],
                "temperature": 0, "stream": False,
            }
            before_error = state.snapshot()["calls"]
            error_status, error_headers, error_raw = _proxy_request(host, port, error_payload)
            error_calls = state.snapshot()["calls"] - before_error
            if error_status != 429 or json.loads(error_raw) != {"error": {"message": "rate limited fixture"}}:
                raise AssertionError(f"upstream HTTP error mapping drift: {error_status} {error_headers} {error_raw!r}")
            if error_calls != transport["upstream_attempts_per_request"]:
                raise AssertionError(f"HTTP error was retried: calls={error_calls}")
            if error_headers.get("retry-after") != "7" or error_headers.get("x-upstream-fixture") != "rate-limit" or "authorization" in error_headers:
                raise AssertionError(f"HTTP error response header mapping drift: {error_headers}")
            if not error_headers.get("x-syntavra-evidence", "").startswith("sc://sha256/"):
                raise AssertionError(f"HTTP error was not evidence committed: {error_headers}")

            timeout_payload = {
                "model": "gpt-timeout", "messages": [{"role": "user", "content": "timeout"}],
                "temperature": 0, "stream": False,
            }
            with patch("syntavra_runtime.provider_proxy.urllib.request.urlopen", side_effect=TimeoutError("fixture-timeout")) as mocked_urlopen:
                timeout_status, _, timeout_raw = _proxy_request(host, port, timeout_payload)
            timeout_json = json.loads(timeout_raw)
            if timeout_status != transport["timeout_error_status"] or timeout_json != {"error": "TimeoutError", "detail": transport["transport_error_detail"]}:
                raise AssertionError(f"timeout mapping drift: {timeout_status} {timeout_json}")
            if mocked_urlopen.call_count != transport["upstream_attempts_per_request"]:
                raise AssertionError(f"timeout path retried unexpectedly: {mocked_urlopen.call_count}")
            timeout_kw = mocked_urlopen.call_args.kwargs
            if timeout_kw.get("timeout") != config.timeout_seconds:
                raise AssertionError(f"configured timeout not forwarded: {mocked_urlopen.call_args}")

            before_missing = state.snapshot()["calls"]
            missing_payload = {
                "model": "gpt-missing-credential", "messages": [{"role": "user", "content": "credential"}],
                "temperature": 0, "stream": False,
            }
            with patch.dict(os.environ, {"TEST_PROVIDER_KEY": ""}, clear=False):
                missing_status, _, missing_raw = _proxy_request(host, port, missing_payload)
            missing_json = json.loads(missing_raw)
            if missing_status != 502 or missing_json != {"error": "RuntimeError", "detail": transport["transport_error_detail"]}:
                raise AssertionError(f"missing credential mapping drift: {missing_status} {missing_json}")
            if state.snapshot()["calls"] != before_missing:
                raise AssertionError("missing credential request reached upstream")

            unauth_status, unauth = _control(host, port, "/_syntavra/health")
            health_status, health = _control(host, port, "/_syntavra/health", CONTROL_TOKEN)
            if unauth_status != transport["control_unauthorized_status"] or unauth.get("error") != transport["control_unauthorized_error"]:
                raise AssertionError(f"control authentication drift: {unauth_status} {unauth}")
            if health_status != 200 or health.get("ok") is not True or health.get("stream_mode") != transport["stream_mode"]:
                raise AssertionError(f"proxy health drift: {health_status} {health}")

            return {
                "normal": {
                    "status": normal_status,
                    "upstream_attempts": after_normal["calls"] - before,
                    "upstream_authorization": "server-credential",
                    "client_authorization_forwarded": False,
                    "internal_request_id": True,
                    "prompt_cache_key_present": True,
                    "replay_header": normal_headers.get("x-syntavra-replay"),
                    "evidence_header": True,
                    "response_authorization_forwarded": False,
                },
                "replay": {
                    "status": replay_status,
                    "replay_header": replay_headers.get("x-syntavra-replay"),
                    "upstream_attempts": after_replay["calls"] - after_normal["calls"],
                },
                "http_error": {
                    "status": error_status,
                    "body": json.loads(error_raw),
                    "retry_after": error_headers.get("retry-after"),
                    "upstream_attempts": error_calls,
                    "evidence_header": True,
                    "credential_header_forwarded": False,
                },
                "timeout": {
                    "status": timeout_status,
                    "body": timeout_json,
                    "upstream_attempts": mocked_urlopen.call_count,
                    "configured_timeout_seconds": config.timeout_seconds,
                },
                "missing_credential": {
                    "status": missing_status,
                    "body": missing_json,
                    "upstream_attempts": state.snapshot()["calls"] - before_missing,
                },
                "control": {
                    "unauthorized_status": unauth_status,
                    "unauthorized_error": unauth.get("error"),
                    "health_status": health_status,
                    "health_ok": health.get("ok"),
                },
                "retry_policy": transport["retry_policy"],
            }
    finally:
        try:
            proxy.shutdown()
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-provider-proxy-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        state.mkdir()
        (project / ".git").mkdir()
        routes = _family_routes(fixture)
        gateway = _provider_gateway_contract(repo, project, state, fixture)
        helpers = _helper_contract(repo, project, state, fixture)
        transport = _proxy_transport_contract(root, fixture)
    return {
        "ok": True,
        "schema_version": 1,
        "family": "provider-proxy",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "routes": routes,
        "provider_gateway": gateway,
        "helpers": helpers,
        "transport": transport,
        "exit_policy": {
            "success": 0,
            "python_application_error": 4,
            "provider_replay_miss": 4,
            "provider_verify_failure": 3,
            "argparse_error": 2,
        },
        "nondeterministic_fields": [
            "request IDs",
            "evidence handles and encrypted object bytes",
            "usage receipt sequence/timestamps",
            "provider-account updated_at timestamps",
            "temporary project/state paths",
            "proxy listen port when configured as 0",
            "insight latency measurements",
        ],
        "network_boundary": "localhost-only deterministic HTTP fixture; no live provider or SaaS endpoint",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Python provider/proxy reference contract")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "provider-proxy",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
