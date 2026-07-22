from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.provider_proxy import (
    ProviderProxyRuntime,
    ProxyConfig,
    _new_request_id,
    _validated_header,
    _validated_header_name,
    _validated_header_value,
)
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger


class ProviderProxyHeaderSecurityTests(unittest.TestCase):
    def runtime(self, root: Path, **overrides: object) -> ProviderProxyRuntime:
        evidence = EvidenceStore(root / "evidence", project_id="header-security")
        ledger = UsageReceiptLedger(root / "usage.sqlite3", signing_key=b"header-security-key")
        gateway = ProviderGateway(root / "gateway.sqlite3", evidence=evidence, usage_ledger=ledger)
        values = {
            "provider": "openai",
            "upstream_base": "https://api.example.invalid",
            "credential_env": "TEST_PROVIDER_KEY",
            "control_token_env": "TEST_CONTROL_TOKEN",
        }
        values.update(overrides)
        return ProviderProxyRuntime(
            ProxyConfig(**values),
            gateway=gateway,
            insight_path=root / "insights.sqlite3",
        )

    def test_header_name_rejects_response_splitting_and_invalid_tokens(self) -> None:
        for value in ("X-Test\r\nInjected", "X Test", "", "X-Test:\x00"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _validated_header_name(value)
        self.assertEqual(_validated_header_name("X-Syntavra-Test"), "X-Syntavra-Test")

    def test_header_value_rejects_crlf_nul_controls_unicode_and_oversize(self) -> None:
        invalid = (
            "safe\r\nX-Injected: yes",
            "safe\x00tail",
            "safe\x01tail",
            "snowman-☃",
            "x" * 8193,
        )
        for value in invalid:
            with self.subTest(value=value[:40]):
                with self.assertRaises(ValueError):
                    _validated_header_value(value)
        self.assertEqual(_validated_header_value("Bearer abc-123"), "Bearer abc-123")
        self.assertEqual(_validated_header("X-Test", "ok"), ("X-Test", "ok"))

    def test_proxy_config_rejects_malicious_credential_header_and_prefix(self) -> None:
        with self.assertRaises(ValueError):
            ProxyConfig(
                provider="openai",
                upstream_base="https://api.example.invalid",
                credential_header="Authorization\r\nX-Evil",
            ).validate()
        with self.assertRaises(ValueError):
            ProxyConfig(
                provider="openai",
                upstream_base="https://api.example.invalid",
                credential_prefix="Bearer safe\r\nX-Evil: ",
            ).validate()

    def test_environment_credential_is_validated_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary))
            with patch.dict(
                os.environ,
                {
                    "TEST_PROVIDER_KEY": "secret\r\nX-Evil: yes",
                    "TEST_CONTROL_TOKEN": "c" * 32,
                },
                clear=False,
            ):
                with self.assertRaises(ValueError):
                    runtime._credential()

    def test_incoming_tainted_headers_are_dropped_and_request_id_is_internal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary), credential_env="")
            incoming = {
                "Accept": "application/json",
                "X-Request-ID": "attacker\r\nX-Evil: injected",
                "X-Syntavra-Client-Trace": "bad\nheader",
                "Authorization": "Bearer client-secret",
            }
            generated = _new_request_id()
            headers = runtime._headers(incoming, 7, generated)
            self.assertEqual(headers["X-Request-ID"], generated)
            self.assertNotEqual(headers["X-Request-ID"], incoming["X-Request-ID"])
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("X-Syntavra-Client-Trace", headers)
            self.assertEqual(headers["Accept"], "application/json")
            self.assertRegex(generated, r"^sc-[0-9a-f]{32}$")

    def test_upstream_response_headers_are_sanitized_before_forward_or_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.runtime(Path(temporary), credential_env="")
            safe = runtime._safe_response_headers(
                {
                    "Content-Type": "application/json",
                    "X-Safe": "yes",
                    "X-Bad": "value\r\nX-Injected: yes",
                    "Bad Name": "value",
                    "X-Unicode": "☃",
                }
            )
            self.assertEqual(safe["Content-Type"], "application/json")
            self.assertEqual(safe["X-Safe"], "yes")
            self.assertNotIn("X-Bad", safe)
            self.assertNotIn("Bad Name", safe)
            self.assertNotIn("X-Unicode", safe)


if __name__ == "__main__":
    unittest.main()
