#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

# One-shot parity repair. The workflow deletes this helper after all Python,
# Rust, and cross-engine transport gates pass on the same checkout.
ROOT = Path(__file__).resolve().parents[1]
PY_PROXY = ROOT / "syntavra_runtime/provider_proxy.py"
RS_PROXY = ROOT / "crates/syntavra-cli/src/native_remaining71_provider_proxy.rs"
PY_TEST = ROOT / "tests/runtime/test_provider_proxy_v4.py"
DIFF = ROOT / "tools/validate_remaining71_provider_proxy_differential.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one old match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        PY_PROXY,
        '''                if not isinstance(payload, Mapping):\n                    self._json(HTTPStatus.BAD_REQUEST, {"error": "provider-request-must-be-object"})\n                    return\n                try:\n                    plan = runtime._prepare(payload)\n''',
        '''                if not isinstance(payload, Mapping):\n                    self._json(HTTPStatus.BAD_REQUEST, {"error": "provider-request-must-be-object"})\n                    return\n                # Validate the request target before cache/replay lookup. Otherwise\n                # a previously cached payload could return a replay hit for an\n                # absolute-form target and bypass the fixed-origin boundary.\n                try:\n                    upstream_url = runtime._upstream_url(self.path)\n                except ValueError:\n                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "ValueError", "detail": "upstream request failed"})\n                    return\n                try:\n                    plan = runtime._prepare(payload)\n''',
        "python pre-replay target validation",
    )
    replace_once(
        PY_PROXY,
        '''                try:\n                    url = runtime._upstream_url(self.path)\n                    request = urllib.request.Request(\n                        url,\n''',
        '''                try:\n                    request = urllib.request.Request(\n                        upstream_url,\n''',
        "python validated URL reuse",
    )

    replace_once(
        RS_PROXY,
        '''        if let Some(handle) = plan["replay_response_handle"]\n            .as_str()\n            .filter(|value| !value.is_empty())\n        {\n            headers.push(("X-Syntavra-Evidence".to_owned(), handle.to_owned()));\n        }\n        write_response(stream, 200, &headers, &body)?;\n''',
        '''        // Python's replay surface intentionally exposes the request/replay\n        // decision but does not label the semantic replay object as raw transport\n        // evidence. Keep the native header contract identical.\n        write_response(stream, 200, &headers, &body)?;\n''',
        "rust replay evidence header",
    )

    replace_once(
        DIFF,
        '''            "evidence": True,\n            "upstream_calls": 1,\n''',
        '''            "evidence": False,\n            "upstream_calls": 1,\n''',
        "differential replay evidence expectation",
    )

    test_anchor = '''    def test_control_endpoints_require_token_even_on_loopback(self) -> None:\n'''
    test_block = '''    def test_cached_replay_cannot_bypass_absolute_target_rejection(self) -> None:\n        status, headers, raw = self.request(self.payload())\n        self.assertEqual(status, 200)\n        self.assertEqual(headers["X-Syntavra-Replay"], "miss")\n        self.assertEqual(json.loads(raw)["output_text"], "answer")\n        self.assertEqual(_UpstreamHandler.calls, 1)\n\n        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)\n        connection.putrequest("POST", "http://attacker.invalid/v1/responses", skip_host=True)\n        body = json.dumps(self.payload()).encode("utf-8")\n        connection.putheader("Host", "attacker.invalid")\n        connection.putheader("Content-Type", "application/json")\n        connection.putheader("Content-Length", str(len(body)))\n        connection.endheaders(body)\n        response = connection.getresponse()\n        response.read()\n        self.assertEqual(response.status, 502)\n        connection.close()\n        self.assertEqual(_UpstreamHandler.calls, 1)\n\n'''
    text = PY_TEST.read_text(encoding="utf-8")
    if "def test_cached_replay_cannot_bypass_absolute_target_rejection" not in text:
        count = text.count(test_anchor)
        if count != 1:
            raise SystemExit(f"python regression test anchor: expected one match, found {count}")
        PY_TEST.write_text(text.replace(test_anchor, test_block + test_anchor, 1), encoding="utf-8")

    print("provider proxy parity fix present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
