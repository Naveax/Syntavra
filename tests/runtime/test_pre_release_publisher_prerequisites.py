from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_pre_release_publisher_prerequisites import build_report


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pre-release-publisher-prerequisites.yml"
HEAD = "a" * 40


def protected_environment() -> dict:
    return {
        "name": "pre-release",
        "protection_rules": [
            {
                "id": 1,
                "type": "required_reviewers",
                "reviewers": [{"type": "User", "reviewer": {"login": "release-reviewer"}}],
            }
        ],
    }


class PublisherPrerequisiteReportTests(unittest.TestCase):
    def test_missing_environment_is_incomplete(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment={"missing": True},
            arming_secret_present=False,
            arming_secret_matches=False,
            npm_token_present=False,
            crates_token_present=False,
        )
        self.assertFalse(report["github_environment"]["exists"])
        self.assertFalse(report["github_checkable_ready"])
        self.assertFalse(report["publication_ready"])
        self.assertEqual(report["claim"], "PUBLISHER_GITHUB_PREREQUISITES_INCOMPLETE")
        self.assertFalse(report["publication_performed"])

    def test_protected_environment_and_credentials_make_github_side_ready(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment=protected_environment(),
            arming_secret_present=True,
            arming_secret_matches=True,
            npm_token_present=True,
            crates_token_present=True,
        )
        self.assertTrue(report["github_environment"]["exists"])
        self.assertTrue(report["github_environment"]["required_reviewers"])
        self.assertTrue(report["github_checkable_ready"])
        self.assertFalse(report["external_bindings_verified"])
        self.assertFalse(report["publication_ready"])
        self.assertEqual(report["claim"], "PUBLISHER_EXTERNAL_BINDINGS_UNVERIFIED")

    def test_environment_without_required_reviewers_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment={"name": "pre-release", "protection_rules": []},
            arming_secret_present=True,
            arming_secret_matches=True,
            npm_token_present=True,
            crates_token_present=True,
        )
        self.assertTrue(report["github_environment"]["exists"])
        self.assertFalse(report["github_environment"]["required_reviewers"])
        self.assertFalse(report["github_checkable_ready"])

    def test_wrong_arming_value_fails_closed_without_exposing_value(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment=protected_environment(),
            arming_secret_present=True,
            arming_secret_matches=False,
            npm_token_present=True,
            crates_token_present=True,
        )
        armed = report["credentials"]["syntavra_publish_armed"]
        self.assertTrue(armed["present"])
        self.assertFalse(armed["matches_required_arming_value"])
        self.assertFalse(armed["value_exposed"])
        self.assertFalse(report["github_checkable_ready"])

    def test_missing_registry_token_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment=protected_environment(),
            arming_secret_present=True,
            arming_secret_matches=True,
            npm_token_present=False,
            crates_token_present=True,
        )
        self.assertFalse(report["credentials"]["npm_token"]["present"])
        self.assertFalse(report["github_checkable_ready"])

    def test_exact_head_requires_all_forty_hex_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            build_report(
                exact_head=("a" * 39) + "z",
                environment=protected_environment(),
                arming_secret_present=True,
                arming_secret_matches=True,
                npm_token_present=True,
                crates_token_present=True,
            )

    def test_external_bindings_stay_explicitly_unverified(self) -> None:
        report = build_report(
            exact_head=HEAD,
            environment=protected_environment(),
            arming_secret_present=True,
            arming_secret_matches=True,
            npm_token_present=True,
            crates_token_present=True,
        )
        self.assertEqual(
            report["external_bindings"]["pypi_trusted_publisher"]["status"],
            "external_unverified",
        )
        self.assertEqual(
            report["external_bindings"]["vscode_marketplace_trusted_publisher"]["status"],
            "external_unverified",
        )
        self.assertTrue(report["secret_values_exposed"] is False)


class PublisherPrerequisiteWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_audit_is_manual_for_secret_observation_and_pr_only_for_contract(self) -> None:
        text = self.text
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pull_request:", text)
        self.assertNotIn("\n  push:\n", text)
        self.assertNotIn("\n  schedule:\n", text)
        self.assertIn("github.event_name == 'workflow_dispatch'", text)
        self.assertIn("github.event_name == 'pull_request'", text)

    def test_secret_values_are_only_compared_and_never_printed(self) -> None:
        text = self.text
        for secret in ("SYNTAVRA_PUBLISH_ARMED", "NPM_TOKEN", "CRATES_IO_TOKEN"):
            self.assertIn(f"secrets.{secret}", text)
        self.assertNotIn("echo \"$SYNTAVRA_PUBLISH_ARMED\"", text)
        self.assertNotIn("echo \"$NPM_TOKEN_VALUE\"", text)
        self.assertNotIn("echo \"$CRATES_IO_TOKEN_VALUE\"", text)
        self.assertIn("--arming-secret-present", text)
        self.assertIn("--arming-secret-matches", text)

    def test_environment_is_observed_without_binding_job_to_it(self) -> None:
        text = self.text
        self.assertIn('environments/pre-release', text)
        self.assertIn("tools/check_pre_release_publisher_prerequisites.py", text)
        self.assertNotIn("environment: pre-release", text)
        self.assertIn("deployments: read", text)

    def test_audit_uploads_machine_readable_evidence(self) -> None:
        text = self.text
        self.assertIn("pre-release-publisher-prerequisites-${{ steps.authority.outputs.target_head }}", text)
        self.assertIn("pre-release-publisher-prerequisites.json", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("--require-github-ready", text)


if __name__ == "__main__":
    unittest.main()
