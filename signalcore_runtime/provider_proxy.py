from __future__ import annotations

import json
import os
import secrets
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .competitive_fabric import InsightLedger
from .provider_gateway import ProviderGateway, ProviderPlan
from .security_scan import SecurityStreamScanner, scan_bytes
from .util import canonical_json, sha256_bytes

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}
_CREDENTIAL_HEADERS = {"authorization", "x-api-key", "api-key", "x-goog-api-key"}
_FORWARD_HEADERS = {
    "accept", "content-type", "user-agent", "openai-beta", "openai-organization",
    "openai-project", "anthropic-version", "anthropic-beta", "x-goog-user-project",
    "x-request-id", "traceparent", "tracestate", "idempotency-key",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class ProxyConfig:
    provider: str
    upstream_base: str
    listen_host: str = "127.0.0.1"
    listen_port: int = 8787
    credential_env: str = ""
    credential_header: str = ""
    credential_prefix: str = ""
    control_token_env: str = "SIGNALCORE_PROXY_CONTROL_TOKEN"
    allow_remote: bool = False
    allow_insecure_upstream: bool = False
    tls_cert_file: str = ""
    tls_key_file: str = ""
    cache_policy: str = "auto"
    replay_ttl_seconds: int = 900
    prompt_cache_ttl_seconds: int = 300
    timeout_seconds: float = 180.0
    max_request_bytes: int = 16 * 1024 * 1024
    max_buffered_response_bytes: int = 64 * 1024 * 1024
    spool_memory_bytes: int = 2 * 1024 * 1024
    default_anthropic_version: str = "2023-06-01"
    stream_mode: str = "commit-before-forward"
    max_concurrent_requests: int = 64
    drain_timeout_seconds: float = 30.0
    block_secret_outputs: bool = True
    block_prompt_injection_outputs: bool = True

    def validate(self) -> None:
        if self.listen_port < 0 or self.listen_port > 65535:
            raise ValueError("listen_port must be between 0 and 65535")
        if self.max_request_bytes < 1024 or self.max_buffered_response_bytes < 1024:
            raise ValueError("proxy byte limits must be at least 1024")
        if self.timeout_seconds <= 0 or self.drain_timeout_seconds <= 0:
            raise ValueError("proxy timeouts must be positive")
        if self.max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be positive")
        if self.cache_policy not in {"off", "auto", "read", "read-write"}:
            raise ValueError("invalid cache_policy")
        if self.stream_mode not in {"commit-before-forward"}:
            raise ValueError("only fail-closed commit-before-forward streaming is supported")
        parsed = urllib.parse.urlsplit(self.upstream_base)
        allowed_schemes = {"http", "https"} if self.allow_insecure_upstream else {"https"}
        if parsed.scheme not in allowed_schemes:
            raise ValueError("upstream must use HTTPS unless allow_insecure_upstream is explicit")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("upstream_base must be an origin or fixed base path without credentials/query/fragment")
        if not self.control_token_env:
            raise ValueError("control_token_env is mandatory even for loopback bindings")
        remote = self.listen_host not in _LOOPBACK_HOSTS
        if remote:
            if not self.allow_remote:
                raise ValueError("non-loopback proxy binding requires allow_remote")
            if not self.tls_cert_file or not self.tls_key_file:
                raise ValueError("remote proxy binding requires TLS certificate and key")
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise ValueError("tls_cert_file and tls_key_file must be configured together")


@dataclass(frozen=True)
class RawTransportCapture:
    provider: str
    model: str
    request_hash: str
    status_code: int
    content_type: str
    transport_hash: str
    transport_handle: str
    bytes: int
    visible_preview: str
    secret_types: tuple[str, ...]
    injection_risk: bool
    pii_types: tuple[str, ...] = ()


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args: Any, max_concurrent: int, **kwargs: Any):
        self._slots = threading.BoundedSemaphore(max_concurrent)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: 29\r\n\r\n{\"error\":\"load-shed\"}"
                )
            finally:
                request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class ProviderProxyRuntime:
    """Credential-isolated, authenticated and fail-closed provider proxy.

    Streaming responses are fully spooled, DLP-scanned and committed to encrypted
    exact evidence before any response headers are sent to the client. This trades
    first-token latency for complete transport integrity and eliminates partial
    client responses when capture or storage fails.
    """

    def __init__(self, config: ProxyConfig, *, gateway: ProviderGateway, insight_path: Path):
        config.validate()
        self.config = config
        self.gateway = gateway
        self.insights = InsightLedger(insight_path)
        self._lock = threading.RLock()
        self._active = 0
        self._accepting = True
        self._server: _BoundedThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition(self._lock)

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return self.config.listen_host, self.config.listen_port
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @staticmethod
    def _provider_defaults(provider: str) -> tuple[str, str]:
        canonical = ProviderGateway.capabilities(provider)["provider"]
        if canonical == "openai":
            return "Authorization", "Bearer "
        if canonical == "anthropic":
            return "x-api-key", ""
        if canonical == "gemini":
            return "x-goog-api-key", ""
        return "Authorization", "Bearer "

    def _credential(self) -> tuple[str, str] | None:
        if not self.config.credential_env:
            return None
        value = os.environ.get(self.config.credential_env, "")
        if not value:
            raise RuntimeError(f"missing provider credential environment variable: {self.config.credential_env}")
        default_header, default_prefix = self._provider_defaults(self.config.provider)
        return (
            self.config.credential_header or default_header,
            (self.config.credential_prefix if self.config.credential_prefix else default_prefix) + value,
        )

    def _control_token(self) -> str:
        value = os.environ.get(self.config.control_token_env, "")
        if not value:
            raise RuntimeError(f"missing control token environment variable: {self.config.control_token_env}")
        if len(value) < 32:
            raise RuntimeError("control token must contain at least 32 characters")
        return value

    def _upstream_url(self, raw_target: str) -> str:
        parsed_target = urllib.parse.urlsplit(raw_target)
        if parsed_target.scheme or parsed_target.netloc:
            raise ValueError("absolute proxy targets are forbidden")
        if not parsed_target.path.startswith("/") or "\\" in parsed_target.path:
            raise ValueError("request target must be origin-form")
        base = urllib.parse.urlsplit(self.config.upstream_base)
        base_path = base.path.rstrip("/")
        joined_path = f"{base_path}{parsed_target.path}" if base_path else parsed_target.path
        return urllib.parse.urlunsplit((base.scheme, base.netloc, joined_path, parsed_target.query, ""))

    def _headers(self, incoming: Mapping[str, str], body_length: int, request_id: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in incoming.items():
            name = key.casefold()
            if name in _HOP_BY_HOP or name in _CREDENTIAL_HEADERS:
                continue
            if name in _FORWARD_HEADERS or name.startswith("x-signalcore-client-"):
                result[key] = value
        result["Content-Length"] = str(body_length)
        result.setdefault("Content-Type", "application/json")
        result["X-Request-ID"] = request_id
        canonical = ProviderGateway.capabilities(self.config.provider)["provider"]
        if canonical == "anthropic":
            result.setdefault("anthropic-version", self.config.default_anthropic_version)
        credential = self._credential()
        if credential:
            result[credential[0]] = credential[1]
        return result

    def _raw_capture(
        self,
        plan: ProviderPlan,
        body: bytes,
        *,
        status_code: int,
        content_type: str,
        response_headers: Mapping[str, str],
        security: Any | None = None,
        request_id: str,
    ) -> RawTransportCapture:
        transport_hash = sha256_bytes(body)
        handle = self.gateway.evidence.put(
            body,
            kind="provider-response-transport",
            metadata={
                "provider": plan.provider,
                "model": plan.model,
                "request_hash": plan.request_hash,
                "transport_hash": transport_hash,
                "request_id": request_id,
                "status_code": int(status_code),
                "content_type": content_type,
                "response_headers": {
                    key: value for key, value in response_headers.items()
                    if key.casefold() not in _HOP_BY_HOP and key.casefold() not in _CREDENTIAL_HEADERS
                },
            },
            reference=f"provider-response:{request_id}",
        )
        security = security or scan_bytes(body)
        preview_raw = security.redacted_text.encode("utf-8")
        marker = "\n[… encrypted exact provider transport stored as evidence …]"
        if len(preview_raw) > 4096:
            keep = max(0, 4096 - len(marker.encode("utf-8")))
            preview = preview_raw[:keep].decode("utf-8", errors="ignore").rstrip() + marker
        else:
            preview = security.redacted_text
        return RawTransportCapture(
            provider=plan.provider,
            model=plan.model,
            request_hash=plan.request_hash,
            status_code=int(status_code),
            content_type=content_type,
            transport_hash=transport_hash,
            transport_handle=handle,
            bytes=len(body),
            visible_preview=preview,
            secret_types=security.secret_types,
            injection_risk=security.injection_risk,
            pii_types=getattr(security, "pii_types", ()),
        )

    def _prepare(self, payload: Mapping[str, Any]) -> ProviderPlan:
        return self.gateway.prepare(
            self.config.provider,
            payload,
            model=str(payload.get("model") or ""),
            cache_policy=self.config.cache_policy,
            replay_ttl_seconds=self.config.replay_ttl_seconds,
            prompt_cache_ttl_seconds=self.config.prompt_cache_ttl_seconds,
        )

    def _enter(self) -> bool:
        with self._condition:
            if not self._accepting:
                return False
            self._active += 1
            return True

    def _exit(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            accepting = self._accepting
        return {
            "ok": accepting,
            "ready": accepting,
            "provider": ProviderGateway.capabilities(self.config.provider)["provider"],
            "listen": {"host": self.address[0], "port": self.address[1], "tls": bool(self.config.tls_cert_file)},
            "upstream_origin_hash": sha256_bytes(self.config.upstream_base.encode("utf-8")),
            "cache_policy": self.config.cache_policy,
            "stream_mode": self.config.stream_mode,
            "active_requests": active,
            "gateway": self.gateway.stats(),
            "insights": self.insights.metrics(),
        }

    def verify(self) -> dict[str, Any]:
        gateway = self.gateway.verify()
        insights_ok = self.insights.state.integrity_check()
        try:
            token_ok = len(self._control_token()) >= 32
        except RuntimeError:
            token_ok = False
        return {
            "ok": bool(gateway["ok"] and insights_ok and token_ok),
            "gateway": gateway,
            "insights_database_integrity": insights_ok,
            "control_authentication": token_ok,
            "encrypted_evidence": bool(self.gateway.evidence.stats().get("encrypted")),
        }

    def _control_allowed(self, headers: Mapping[str, str]) -> bool:
        try:
            expected = self._control_token()
        except RuntimeError:
            return False
        supplied = headers.get("Authorization", "")
        return secrets.compare_digest(supplied, f"Bearer {expected}")

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "SignalCoreProviderProxy/0.6"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _json(self, status: int, payload: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> None:
                body = canonical_json(dict(payload))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _control(self) -> bool:
                if runtime._control_allowed(self.headers):
                    return True
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid-control-token"})
                return False

            def do_GET(self) -> None:
                target = urllib.parse.urlsplit(self.path).path
                if target == "/_signalcore/health":
                    if self._control():
                        self._json(HTTPStatus.OK, runtime.status())
                    return
                if target == "/_signalcore/ready":
                    if self._control():
                        status = runtime.status()
                        self._json(HTTPStatus.OK if status["ready"] else HTTPStatus.SERVICE_UNAVAILABLE, {"ready": status["ready"], "active_requests": status["active_requests"]})
                    return
                if target == "/_signalcore/verify":
                    if self._control():
                        result = runtime.verify()
                        self._json(HTTPStatus.OK if result["ok"] else HTTPStatus.CONFLICT, result)
                    return
                if not runtime._enter():
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "proxy-draining"})
                    return
                try:
                    self._proxy_without_json_body("GET")
                finally:
                    runtime._exit()

            def do_POST(self) -> None:
                if not runtime._enter():
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "proxy-draining"})
                    return
                started = time.perf_counter()
                try:
                    self._post(started)
                finally:
                    runtime._exit()

            def _post(self, started: float) -> None:
                request_id = self.headers.get("X-Request-ID") or "sc-" + uuid.uuid4().hex
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-content-length"})
                    return
                if length <= 0:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "json-body-required"})
                    return
                if length > runtime.config.max_request_bytes:
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request-body-too-large"})
                    return
                raw_request = self.rfile.read(length)
                if len(raw_request) != length:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "incomplete-request-body"})
                    return
                try:
                    payload = json.loads(raw_request)
                except json.JSONDecodeError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid-json", "detail": str(exc)})
                    return
                if not isinstance(payload, Mapping):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "provider-request-must-be-object"})
                    return
                try:
                    plan = runtime._prepare(payload)
                except Exception as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": type(exc).__name__, "detail": str(exc)})
                    return
                streaming = bool(plan.prepared_request.get("stream") or plan.prepared_request.get("streaming"))
                if plan.replay_hit and not streaming:
                    replay = runtime.gateway.replay(plan)
                    if replay is not None:
                        body = canonical_json(replay)
                        self.send_response(HTTPStatus.OK)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("X-SignalCore-Replay", "hit")
                        self.send_header("X-SignalCore-Request-Handle", plan.request_handle)
                        self.send_header("X-Request-ID", request_id)
                        self.end_headers()
                        self.wfile.write(body)
                        duration = (time.perf_counter() - started) * 1000
                        runtime.insights.record(
                            "provider-proxy", family=plan.provider, host="proxy",
                            raw_bytes=len(body), visible_bytes=len(body), latency_ms=duration,
                            success=True, cache_hit=True,
                            metadata={"status": 200, "streaming": False, "request_id": request_id},
                        )
                        return
                prepared_body = canonical_json(plan.prepared_request)
                try:
                    url = runtime._upstream_url(self.path)
                    request = urllib.request.Request(
                        url,
                        data=prepared_body,
                        headers=runtime._headers(self.headers, len(prepared_body), request_id),
                        method="POST",
                    )
                    try:
                        response = urllib.request.urlopen(request, timeout=runtime.config.timeout_seconds)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    status = int(response.status)
                    response_headers = {str(key): str(value) for key, value in response.headers.items()}
                    content_type = response.headers.get("Content-Type", "application/octet-stream")
                    self._commit_then_forward(
                        response, plan, status, response_headers, content_type, started,
                        request_id=request_id, semantic=not streaming, streaming=streaming,
                    )
                except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
                    duration = (time.perf_counter() - started) * 1000
                    runtime.insights.record(
                        "provider-proxy", family=plan.provider, host="proxy",
                        latency_ms=duration, success=False,
                        metadata={"error": type(exc).__name__, "request_id": request_id},
                    )
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": type(exc).__name__, "detail": str(exc)})

            def _forward_response_headers(
                self,
                status: int,
                headers: Mapping[str, str],
                *,
                content_length: int,
                plan: ProviderPlan,
                evidence_handle: str,
                streaming: bool,
                request_id: str,
            ) -> None:
                self.send_response(status)
                for key, value in headers.items():
                    name = key.casefold()
                    if name in _HOP_BY_HOP or name in _CREDENTIAL_HEADERS or name == "content-length":
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(content_length))
                self.send_header("X-SignalCore-Replay", "miss")
                self.send_header("X-SignalCore-Request-Handle", plan.request_handle)
                self.send_header("X-SignalCore-Capture", "complete-before-delivery" if streaming else "complete")
                self.send_header("X-SignalCore-Evidence", evidence_handle)
                self.send_header("X-Request-ID", request_id)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()

            def _commit_then_forward(
                self,
                response: Any,
                plan: ProviderPlan,
                status: int,
                response_headers: Mapping[str, str],
                content_type: str,
                started: float,
                *,
                request_id: str,
                semantic: bool,
                streaming: bool,
            ) -> None:
                total = 0
                scanner = SecurityStreamScanner()
                with tempfile.SpooledTemporaryFile(max_size=runtime.config.spool_memory_bytes, mode="w+b") as spool:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > runtime.config.max_buffered_response_bytes:
                            self._json(HTTPStatus.BAD_GATEWAY, {"error": "upstream-response-too-large"})
                            return
                        spool.write(chunk)
                        scanner.update(chunk)
                    spool.flush()
                    spool.seek(0)
                    body = spool.read()
                security = scanner.finalize()
                try:
                    raw_capture = runtime._raw_capture(
                        plan, body, status_code=status, content_type=content_type,
                        response_headers=response_headers, security=security, request_id=request_id,
                    )
                except Exception as exc:
                    self._json(
                        HTTPStatus.INSUFFICIENT_STORAGE,
                        {"error": "evidence-commit-failed", "detail": type(exc).__name__},
                    )
                    return
                if (runtime.config.block_secret_outputs and raw_capture.secret_types) or (runtime.config.block_prompt_injection_outputs and raw_capture.injection_risk):
                    runtime.insights.record(
                        "provider-proxy", family=plan.provider, host="proxy",
                        raw_bytes=len(body), visible_bytes=len(raw_capture.visible_preview.encode("utf-8")),
                        latency_ms=(time.perf_counter() - started) * 1000, success=False, cache_hit=False,
                        metadata={
                            "status": status, "streaming": streaming, "request_id": request_id,
                            "blocked": True, "secret_types": raw_capture.secret_types,
                            "injection_risk": raw_capture.injection_risk,
                            "transport_handle": raw_capture.transport_handle,
                        },
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": "stream-dlp-blocked" if streaming else "response-dlp-blocked",
                            "evidence_handle": raw_capture.transport_handle,
                            "secret_types": list(raw_capture.secret_types),
                            "injection_risk": raw_capture.injection_risk,
                        },
                        {"X-Request-ID": request_id},
                    )
                    return
                semantic_handle = ""
                if semantic and "json" in content_type.casefold():
                    try:
                        decoded = json.loads(body)
                    except json.JSONDecodeError:
                        decoded = None
                    if isinstance(decoded, Mapping):
                        semantic_capture = runtime.gateway.capture(
                            plan,
                            decoded,
                            store_replay=200 <= status < 300,
                            replay_ttl_seconds=runtime.config.replay_ttl_seconds,
                        )
                        semantic_handle = semantic_capture.response_handle
                self._forward_response_headers(
                    status, response_headers, content_length=len(body), plan=plan,
                    evidence_handle=raw_capture.transport_handle, streaming=streaming, request_id=request_id,
                )
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                duration = (time.perf_counter() - started) * 1000
                runtime.insights.record(
                    "provider-proxy", family=plan.provider, host="proxy",
                    raw_bytes=len(body), visible_bytes=len(raw_capture.visible_preview.encode("utf-8")),
                    latency_ms=duration, success=200 <= status < 500, cache_hit=False,
                    metadata={
                        "status": status, "streaming": streaming,
                        "transport_handle": raw_capture.transport_handle,
                        "semantic_handle": semantic_handle,
                        "request_id": request_id,
                        "secret_types": raw_capture.secret_types,
                        "pii_types": raw_capture.pii_types,
                    },
                )

            def _proxy_without_json_body(self, method: str) -> None:
                started = time.perf_counter()
                request_id = self.headers.get("X-Request-ID") or "sc-" + uuid.uuid4().hex
                try:
                    url = runtime._upstream_url(self.path)
                    headers = runtime._headers(self.headers, 0, request_id)
                    headers.pop("Content-Length", None)
                    request = urllib.request.Request(url, headers=headers, method=method)
                    try:
                        response = urllib.request.urlopen(request, timeout=runtime.config.timeout_seconds)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    body = response.read(runtime.config.max_buffered_response_bytes + 1)
                    if len(body) > runtime.config.max_buffered_response_bytes:
                        self._json(HTTPStatus.BAD_GATEWAY, {"error": "upstream-response-too-large"})
                        return
                    status = int(response.status)
                    pseudo_plan = runtime.gateway.prepare(
                        runtime.config.provider,
                        {"model": "transport-only", "method": method, "path_hash": sha256_bytes(self.path.encode("utf-8")), "temperature": 0},
                        cache_policy="off",
                    )
                    capture = runtime._raw_capture(
                        pseudo_plan, body, status_code=status,
                        content_type=response.headers.get("Content-Type", "application/octet-stream"),
                        response_headers={str(k): str(v) for k, v in response.headers.items()},
                        request_id=request_id,
                    )
                    self._forward_response_headers(
                        status, {str(k): str(v) for k, v in response.headers.items()},
                        content_length=len(body), plan=pseudo_plan,
                        evidence_handle=capture.transport_handle, streaming=False, request_id=request_id,
                    )
                    self.wfile.write(body)
                    runtime.insights.record(
                        "provider-proxy", family="passthrough", host="proxy",
                        raw_bytes=len(body), visible_bytes=len(capture.visible_preview.encode("utf-8")),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        success=200 <= status < 500,
                        metadata={"status": status, "method": method, "request_id": request_id, "transport_handle": capture.transport_handle},
                    )
                except Exception as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": type(exc).__name__, "detail": str(exc)})

        return Handler

    def _build_server(self) -> _BoundedThreadingHTTPServer:
        server = _BoundedThreadingHTTPServer(
            (self.config.listen_host, self.config.listen_port),
            self._handler_type(),
            max_concurrent=self.config.max_concurrent_requests,
        )
        if self.config.tls_cert_file:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.config.tls_cert_file, self.config.tls_key_file)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        return server

    def start(self) -> tuple[str, int]:
        self._control_token()
        if self._server is not None:
            return self.address
        with self._condition:
            self._accepting = True
        self._server = self._build_server()
        self._thread = threading.Thread(target=self._server.serve_forever, name="signalcore-provider-proxy", daemon=True)
        self._thread.start()
        return self.address

    def serve_forever(self) -> None:
        self._control_token()
        if self._server is not None:
            raise RuntimeError("proxy already started")
        with self._condition:
            self._accepting = True
        self._server = self._build_server()
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()
            self._server = None

    def shutdown(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return
        with self._condition:
            self._accepting = False
            deadline = time.monotonic() + self.config.drain_timeout_seconds
            while self._active > 0 and time.monotonic() < deadline:
                self._condition.wait(timeout=min(0.2, deadline - time.monotonic()))
        server.shutdown()
        server.server_close()
        self._server = None
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None
