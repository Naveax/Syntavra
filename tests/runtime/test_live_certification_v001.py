from __future__ import annotations

import unittest

from syntavra_runtime.live_certification import LiveCertificationGate, LiveIntegrationReceipt


class LiveCertificationV001Tests(unittest.TestCase):
    @staticmethod
    def _receipt(index: int, integration_id: str = "codex", family: str = "host") -> LiveIntegrationReceipt:
        return LiveIntegrationReceipt(
            receipt_id=f"live-{integration_id}-{index}",
            integration_id=integration_id,
            family=family,
            observed_at=f"2026-07-{index + 1:02d}T12:00:00+00:00",
            syntavra_version="0.0.1",
            syntavra_channel="pre-release",
            adapter_version="adapter-contract-v1",
            operating_system=("linux", "windows", "macos")[index % 3],
            runtime_version="python-3.13",
            environment_hash="a" * 64,
            config_hash="b" * 64,
            harness_commit="c" * 40,
            artifact_hash="d" * 64,
            install_succeeded=True,
            doctor_passed=True,
            request_succeeded=True,
            response_succeeded=True,
            streaming_verified=True,
            provider_usage_captured=True,
            tool_routing_verified=True,
            session_continuity_verified=True,
            rollback_verified=True,
            external=True,
            synthetic=False,
            metadata={},
        )

    def test_three_external_receipts_across_two_operating_systems_certify(self) -> None:
        rows = [self._receipt(index) for index in range(3)]
        result = LiveCertificationGate.evaluate(rows, integration_id="codex")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "LIVE_INTEGRATION_CERTIFIED")
        self.assertEqual(result["certified_integrations"], ["codex"])

    def test_internal_or_synthetic_receipt_never_certifies(self) -> None:
        row = self._receipt(0)
        internal = LiveIntegrationReceipt(**{**row.__dict__, "external": False, "synthetic": True})
        result = LiveCertificationGate.evaluate([internal], integration_id="codex")
        self.assertFalse(result["ok"])
        self.assertEqual(result["claim"], "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN")
        reasons = result["invalid"][0]["reasons"]
        self.assertIn("not-external", reasons)
        self.assertIn("synthetic-receipt", reasons)

    def test_host_certification_requires_routing_and_continuity(self) -> None:
        rows = [
            LiveIntegrationReceipt(**{
                **self._receipt(index).__dict__,
                "tool_routing_verified": False,
                "session_continuity_verified": False,
            })
            for index in range(3)
        ]
        result = LiveCertificationGate.evaluate(rows, integration_id="codex")
        self.assertFalse(result["ok"])
        flattened = {reason for item in result["invalid"] for reason in item["reasons"]}
        self.assertIn("tool-routing-verified-required", flattened)
        self.assertIn("session-continuity-verified-required", flattened)

    def test_provider_certification_requires_usage_and_streaming(self) -> None:
        rows = [self._receipt(index, "openai", "provider") for index in range(3)]
        result = LiveCertificationGate.evaluate(rows, integration_id="openai")
        self.assertTrue(result["ok"], result)
        broken = [LiveIntegrationReceipt(**{**row.__dict__, "provider_usage_captured": False}) for row in rows]
        result = LiveCertificationGate.evaluate(broken, integration_id="openai")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
