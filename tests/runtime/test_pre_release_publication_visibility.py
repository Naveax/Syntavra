from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.check_pre_release_publication_visibility import (
    build_target_report,
    verify_with_retries,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
CHECKER = ROOT / "tools" / "check_pre_release_publication_visibility.py"


def occupied(url: str) -> dict:
    return {"status": "occupied", "http_status": 200, "error": None, "url": url}


def available(url: str) -> dict:
    return {"status": "available", "http_status": 404, "error": None, "url": url}


class PublicationVisibilityReportTests(unittest.TestCase):
    def test_python_exact_version_visibility_is_verified(self) -> None:
        report = build_target_report("python", http_probe=occupied)
        self.assertTrue(report["visibility_verified"])
        self.assertEqual(report["registry"], "pypi")
        self.assertEqual(report["package"], "syntavra-runtime")
        self.assertEqual(report["claim"], "PUBLIC_VERSION_VISIBLE")
        self.assertFalse(report["publication_performed_by_checker"])
        self.assertFalse(report["canonical_readiness_mutated"])

    def test_missing_public_version_fails_closed(self) -> None:
        report = build_target_report("npm", http_probe=available)
        self.assertFalse(report["visibility_verified"])
        self.assertEqual(report["package"], "@syntavra/install")
        self.assertEqual(report["claim"], "PUBLIC_VERSION_NOT_YET_VISIBLE")

    def test_rust_targets_are_exact(self) -> None:
        expected = {
            "rust_contracts": "syntavra-contracts",
            "rust_core": "syntavra-core",
            "rust_cli": "syntavra-cli",
        }
        for target, package in expected.items():
            with self.subTest(target=target):
                report = build_target_report(target, http_probe=occupied)
                self.assertEqual(report["registry"], "crates.io")
                self.assertEqual(report["package"], package)
                self.assertTrue(report["visibility_verified"])

    def test_vscode_exact_version_visibility_is_verified(self) -> None:
        calls: list[tuple[str, str]] = []

        def probe(extension_id: str, version: str) -> dict:
            calls.append((extension_id, version))
            return {
                "status": "occupied",
                "extension_exists": True,
                "version_exists": True,
                "observed_versions": [version],
                "error": None,
            }

        report = build_target_report("vscode", vsce_probe=probe)
        self.assertTrue(report["visibility_verified"])
        self.assertEqual(calls, [("naveax.syntavra-vscode", "0.0.1")])

    def test_retry_stops_at_first_visible_receipt(self) -> None:
        states = iter((available("x"), available("x"), occupied("x")))
        sleeps: list[float] = []

        def probe(_: str) -> dict:
            return next(states)

        report = verify_with_retries(
            "npm_sdk",
            attempts=5,
            delay_seconds=0.25,
            http_probe=probe,
            sleeper=sleeps.append,
        )
        self.assertTrue(report["visibility_verified"])
        self.assertEqual(report["attempts_used"], 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_retry_exhaustion_remains_unverified(self) -> None:
        sleeps: list[float] = []
        report = verify_with_retries(
            "legacy_native_companion",
            attempts=3,
            delay_seconds=0.1,
            http_probe=available,
            sleeper=sleeps.append,
        )
        self.assertFalse(report["visibility_verified"])
        self.assertEqual(report["attempts_used"], 3)
        self.assertEqual(sleeps, [0.1, 0.1])


class SerializedPublicationWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.checker = CHECKER.read_text(encoding="utf-8")

    def _job(self, name: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            self.workflow,
        )
        self.assertIsNotNone(match, name)
        return match.group("body")

    def test_irreversible_jobs_form_one_serial_chain(self) -> None:
        expected_predecessor = {
            "publish-rust-production": None,
            "publish-npm-installer": "publish-rust-production",
            "publish-npm-sdk": "publish-npm-installer",
            "publish-pypi": "publish-npm-sdk",
            "publish-vscode": "publish-pypi",
            "publish-legacy-native": "publish-vscode",
        }
        for job, predecessor in expected_predecessor.items():
            with self.subTest(job=job):
                body = self._job(job)
                self.assertIn("- authority", body)
                self.assertIn("- credential-preflight", body)
                if predecessor:
                    self.assertIn(f"- {predecessor}", body)

    def test_each_registry_write_is_followed_by_public_visibility_verification(self) -> None:
        expectations = {
            "publish-npm-installer": "--target npm",
            "publish-npm-sdk": "--target npm_sdk",
            "publish-pypi": "--target python",
            "publish-vscode": "--target vscode",
            "publish-legacy-native": "--target legacy_native_companion",
        }
        for job, target in expectations.items():
            with self.subTest(job=job):
                body = self._job(job)
                self.assertIn("check_pre_release_publication_visibility.py", body)
                self.assertIn(target, body)
                self.assertIn("actions/upload-artifact@v4", body)
                self.assertIn("if: always()", body)

    def test_rust_dependency_chain_waits_for_each_exact_crate(self) -> None:
        body = self._job("publish-rust-production")
        sequence = (
            "cargo +1.82.0 publish --locked -p syntavra-contracts",
            "--target rust_contracts",
            "cargo +1.82.0 publish --locked -p syntavra-core",
            "--target rust_core",
            "cargo +1.82.0 publish --locked -p syntavra-cli",
            "--target rust_cli",
        )
        positions = [body.index(token) for token in sequence]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("actions/upload-artifact@v4", body)
        self.assertIn("if: always()", body)

    def test_visibility_checker_contains_no_registry_write_command(self) -> None:
        text = self.checker
        self.assertNotIn("npm publish", text)
        self.assertNotIn("cargo publish", text)
        self.assertNotIn("gh-action-pypi-publish", text)
        self.assertNotIn("vsce publish", text)
        self.assertIn("publication_performed_by_checker", text)
        self.assertIn("canonical_readiness_mutated", text)


if __name__ == "__main__":
    unittest.main()
