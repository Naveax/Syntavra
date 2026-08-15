from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"


class PreReleasePublishWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_publish_path_is_manual_and_dry_run_is_default(self) -> None:
        text = self.text
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pull_request:", text)
        self.assertNotRegex(text, r"(?m)^\s{2}push:\s*$")
        self.assertNotRegex(text, r"(?m)^\s{2}schedule:\s*$")
        self.assertNotRegex(text, r"(?m)^\s{2}release:\s*$")
        self.assertIn("default: dry-run", text)
        self.assertIn("- dry-run", text)
        self.assertIn("- publish", text)
        self.assertGreaterEqual(text.count("github.event_name == 'workflow_dispatch'"), 8)
        self.assertGreaterEqual(text.count("inputs.mode == 'publish'"), 7)

    def test_publish_mode_requires_exact_head_and_independent_arming(self) -> None:
        text = self.text
        self.assertIn("Exact 40-character main SHA to publish", text)
        self.assertIn('repos/$GITHUB_REPOSITORY/branches/main', text)
        self.assertIn('test "$TARGET_HEAD" = "$current_main"', text)
        self.assertIn("PUBLISH_0_0_1_PRE_RELEASE", text)
        self.assertIn("SYNTAVRA_PUBLISH_ARMED", text)
        self.assertIn("repos/$GITHUB_REPOSITORY/environments/pre-release", text)
        self.assertIn("required_reviewers", text)
        self.assertGreaterEqual(text.count("environment: pre-release"), 6)
        self.assertGreaterEqual(
            text.count("test \"$SYNTAVRA_PUBLISH_ARMED\" = 'PUBLISH_0_0_1_PRE_RELEASE'"),
            6,
        )

    def test_only_canonical_attested_candidate_authority_can_feed_publish(self) -> None:
        text = self.text
        self.assertIn("post-r38-release-provenance-diagnostic.yml/runs", text)
        self.assertIn("pre-release-candidate-receipt-plan.yml/runs", text)
        self.assertIn("release-package-provenance-${{ env.TARGET_HEAD }}", text)
        self.assertIn("pre-release-candidate-receipt-plan-${{ steps.authority.outputs.target_head }}", text)
        self.assertIn("value['source_provenance']['workflow'] == 'Release Package Provenance'", text)
        self.assertIn("value['source_provenance']['run_id'] == expected_source", text)
        self.assertIn("value['publication_performed'] is False", text)
        self.assertIn("REGISTRY_PUBLICATION_NOT_PERFORMED", text)
        for receipt in (
            "receipts['pypi'] is None",
            "receipts['npm_installer'] is None",
            "receipts['npm_sdk'] is None",
            "receipts['vscode_marketplace'] is None",
            "receipts['legacy_native_companion'] is None",
        ):
            self.assertIn(receipt, text)

    def test_current_release_targets_and_publish_commands_are_explicit(self) -> None:
        text = self.text
        for target in (
            "syntavra-runtime",
            "@syntavra/install",
            "@syntavra/sdk",
            "syntavra-vscode",
            "syntavra-cli",
            "syntavra-native",
        ):
            self.assertIn(target, text)

        self.assertIn("pypa/gh-action-pypi-publish@release/v1", text)
        self.assertGreaterEqual(text.count("npm publish \"$package\" --tag next --access public --provenance"), 2)
        rust_commands = (
            "cargo +1.82.0 publish --locked -p syntavra-contracts",
            "cargo +1.82.0 publish --locked -p syntavra-core",
            "cargo +1.82.0 publish --locked -p syntavra-cli",
        )
        positions = [text.index(command) for command in rust_commands]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("@vscode/vsce publish --oidc --pre-release --packagePath", text)
        self.assertIn("inputs.publish_legacy_native", text)

    def test_publish_jobs_have_only_the_permissions_their_registry_needs(self) -> None:
        text = self.text
        self.assertRegex(
            text,
            r"(?s)publish-pypi:.*?permissions:\s*\n\s*contents: read\s*\n\s*actions: read\s*\n\s*id-token: write",
        )
        self.assertRegex(
            text,
            r"(?s)publish-vscode:.*?permissions:\s*\n\s*contents: read\s*\n\s*actions: read\s*\n\s*id-token: write",
        )
        self.assertRegex(
            text,
            r"(?s)publish-rust-production:.*?permissions:\s*\n\s*contents: read",
        )
        self.assertNotIn("contents: write", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("pull-requests: write", text)

    def test_post_publish_job_never_claims_canonical_registry_receipts(self) -> None:
        text = self.text
        self.assertIn("canonical_readiness_mutated': False", text)
        self.assertIn("registry_receipts_admitted': False", text)
        self.assertIn("REGISTRY_PUBLICATION_RECEIPTS_NOT_YET_ADMITTED", text)
        self.assertIn("separate exact-head reviewed change", text)
        self.assertNotRegex(text, r"(?m)^\s*git\s+(commit|push)\b")


if __name__ == "__main__":
    unittest.main()
