from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.provider_proxy import ProviderProxyRuntime, ProxyConfig
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls = 0
    last_authorization = ""
    last_payload: dict = {}
    stream_body = b'data: {"delta":"one"}\n\ndata: [DONE]\n\n'

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        type(self).calls += 1
        type(self).last_authorization = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).last_payload = payload
        if payload.get("stream"):
            body = type(self).stream_body
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        body = json.dumps({
            "id": "resp-proxy", "output_text": "answer",
            "usage": {"input_tokens": 12, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 3},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProviderProxyV6CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _UpstreamHandler.calls = 0
        _UpstreamHandler.last_authorization = ""
        _UpstreamHandler.last_payload = {}
        _UpstreamHandler.stream_body = b'data: {"delta":"one"}\n\ndata: [DONE]\n\n'
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        upstream_host, upstream_port = self.upstream.server_address
        evidence = EvidenceStore(self.root / "evidence", project_id="proxy-test")
        ledger = UsageReceiptLedger(self.root / "usage.sqlite3", signing_key=b"proxy-test-key")
        gateway = ProviderGateway(self.root / "gateway.sqlite3", evidence=evidence, usage_ledger=ledger)
        self.proxy = ProviderProxyRuntime(
            ProxyConfig(
                provider="openai",
                upstream_base=f"http://{upstream_host}:{upstream_port}",
                listen_port=0,
                credential_env="TEST_PROVIDER_KEY",
                control_token_env="TEST_SYNTAVRA_CONTROL_TOKEN",
                allow_insecure_upstream=True,
                timeout_seconds=5,
                max_buffered_response_bytes=1024 * 1024,
            ),
            gateway=gateway,
            insight_path=self.root / "proxy-insights.sqlite3",
        )
        self.env = patch.dict(os.environ, {
            "TEST_PROVIDER_KEY": "server-secret",
            "TEST_SYNTAVRA_CONTROL_TOKEN": "c" * 32,
        }, clear=False)
        self.env.start()
        self.host, self.port = self.proxy.start()

    def tearDown(self) -> None:
        self.proxy.shutdown()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
        self.env.stop()
        self.temp.cleanup()

    def request(self, payload: dict, *, authorization: str = "Bearer client-secret") -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8")
        connection.request("POST", "/v1/responses", body=body, headers={
            "Content-Type": "application/json", "Content-Length": str(len(body)), "Authorization": authorization,
        })
        response = connection.getresponse()
        raw = response.read()
        headers = {key: value for key, value in response.getheaders()}
        status = response.status
        connection.close()
        return status, headers, raw

    @staticmethod
    def payload(*, stream: bool = False) -> dict:
        return {
            "model": "gpt-test",
            "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "question"}],
            "temperature": 0, "stream": stream,
        }

    def control(self, path: str, *, token: str = "") -> tuple[int, dict]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        status = response.status
        connection.close()
        return status, payload

    def test_credential_isolation_replay_and_encrypted_capture(self) -> None:
        status, headers, raw = self.request(self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["output_text"], "answer")
        self.assertEqual(_UpstreamHandler.last_authorization, "Bearer server-secret")
        self.assertNotEqual(_UpstreamHandler.last_authorization, "Bearer client-secret")
        self.assertIn("prompt_cache_key", _UpstreamHandler.last_payload)
        self.assertEqual(headers["X-Syntavra-Replay"], "miss")
        handle = headers["X-Syntavra-Evidence"]
        self.assertTrue(handle.startswith("sc://sha256/"))
        digest = handle.rsplit("/", 1)[1]
        object_path = self.proxy.gateway.evidence.objects / digest[:2] / digest[2:]
        self.assertNotIn(b'"output_text": "answer"', object_path.read_bytes())
        status, headers, raw = self.request(self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Syntavra-Replay"], "hit")
        self.assertEqual(json.loads(raw)["id"], "resp-proxy")
        self.assertEqual(_UpstreamHandler.calls, 1)
        self.assertTrue(self.proxy.verify()["ok"])

    def test_control_endpoints_require_token_even_on_loopback(self) -> None:
        status, payload = self.control("/_syntavra/health")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "invalid-control-token")
        status, health = self.control("/_syntavra/health", token="c" * 32)
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        status, ready = self.control("/_syntavra/ready", token="c" * 32)
        self.assertEqual(status, 200)
        self.assertTrue(ready["ready"])

    def test_stream_is_exact_committed_before_delivery_and_not_replayed(self) -> None:
        status, headers, raw = self.request(self.payload(stream=True))
        self.assertEqual(status, 200)
        self.assertEqual(raw, _UpstreamHandler.stream_body)
        self.assertEqual(headers["X-Syntavra-Capture"], "complete-before-delivery")
        self.assertTrue(headers["X-Syntavra-Evidence"].startswith("sc://sha256/"))
        status, _, _ = self.request(self.payload(stream=True))
        self.assertEqual(status, 200)
        self.assertEqual(_UpstreamHandler.calls, 2)

    def test_stream_dlp_blocks_before_any_provider_bytes_are_delivered(self) -> None:
        _UpstreamHandler.stream_body = b'data: {"delta":"api_key=super-secret-value"}\n\ndata: [DONE]\n\n'
        status, _, raw = self.request(self.payload(stream=True))
        self.assertEqual(status, 502)
        payload = json.loads(raw)
        self.assertEqual(payload["error"], "stream-dlp-blocked")
        self.assertNotIn(b"super-secret-value", raw)
        self.assertTrue(payload["evidence_handle"].startswith("sc://sha256/"))

    def test_remote_binding_requires_tls_and_absolute_targets_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProxyConfig(provider="openai", upstream_base="https://api.example.invalid", listen_host="0.0.0.0", allow_remote=True).validate()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.putrequest("POST", "http://attacker.invalid/v1/responses", skip_host=True)
        body = json.dumps(self.payload()).encode("utf-8")
        connection.putheader("Host", "attacker.invalid")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 502)
        connection.close()
        self.assertEqual(_UpstreamHandler.calls, 0)


if __name__ == "__main__":
    unittest.main()
