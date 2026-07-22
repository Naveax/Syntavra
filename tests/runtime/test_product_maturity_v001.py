from __future__ import annotations

import datetime as dt
import unittest

from syntavra_runtime.product_maturity import (
    DistributionReceipt,
    OnboardingReceipt,
    ProductMaturityGate,
    ReleaseReceipt,
)


class ProductMaturityV001Tests(unittest.TestCase):
    def test_empty_evidence_fails_closed(self) -> None:
        result = ProductMaturityGate.evaluate([], [], [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["claim"], "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN")
        self.assertIn("insufficient-users", result["reasons"])

    def test_external_thresholds_can_open_gate_without_version_change(self) -> None:
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        onboarding = []
        integrations = ("codex", "claude-code", "gemini-cli", "cursor", "vscode-copilot")
        operating_systems = ("windows", "linux", "macos")
        for index in range(1000):
            onboarding.append(OnboardingReceipt(
                receipt_id=f"onboarding-{index}",
                observed_at=(start + dt.timedelta(days=index % 100)).isoformat(),
                user_hash=f"user-{index % 50}",
                repository_hash=f"repo-{index % 100}",
                integration_id=integrations[index % len(integrations)],
                operating_system=operating_systems[index % len(operating_systems)],
                install_wall_time_ms=1000 + index % 100,
                success=True,
                rollback_verified=True,
                doctor_passed=True,
                synthetic=False,
            ))
        distributions = [
            DistributionReceipt("dist-pypi", "2026-04-15T00:00:00+00:00", "pypi", "syntavra-runtime", "0.0.1", 700, 150, True, False),
            DistributionReceipt("dist-npm", "2026-04-15T00:00:00+00:00", "npm", "@syntavra/sdk", "0.0.1", 700, 150, True, False),
        ]
        releases = [
            ReleaseReceipt(f"release-{index}", (start + dt.timedelta(days=30 * index)).isoformat(), f"v0.0.1-pre.{index}", "0.0.1", "pre-release", True, True, True, False)
            for index in range(4)
        ]
        result = ProductMaturityGate.evaluate(
            onboarding,
            distributions,
            releases,
            now=dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["claim"], "PUBLIC_PRODUCT_MATURITY_VERIFIED")
        self.assertEqual(result["version"], "0.0.1")
        self.assertEqual(result["channel"], "pre-release")
        self.assertEqual(result["metrics"]["users"], 50)
        self.assertEqual(result["metrics"]["repositories"], 100)
        self.assertEqual(result["metrics"]["verified_releases"], 4)


if __name__ == "__main__":
    unittest.main()
