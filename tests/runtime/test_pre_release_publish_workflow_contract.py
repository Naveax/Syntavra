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
        self.assertIn("tests/runtime/test_pre_release_registry_availability.py", text)
        self.assertIn("tools/check_pre_release_registry_availability.py", text)

    def test_publish_mode_requires_exact_head_and_independent_arming(self) -> None:
        text = self.text
        self.assertIn("Exact 40-character main SHA to publish", text)
        self.assertIn('[[ "$TARGET_HEAD" =~ ^[0-9a-fA-F]{40}$ ]]', text)
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

    def test_live_registry_preflight_is_fail_closed_for_publish_mode(self) -> None:
        text = self.text
        self.assertIn("Probe live registry version availability", text)
        self.assertIn("python tools/check_pre_release_registry_availability.py", text)
        self.assertIn("--output /tmp/pre-release-registry-preflight.json", text)
        self.assertIn("--require-production-available", text)
        self.assertIn("--require-legacy-available", text)
        self.assertIn("if [ \"$MODE\" = 'publish' ]; then", text)
        self.assertIn("if [ \"$PUBLISH_LEGACY_NATIVE\" = 'true' ]; then", text)
        self.assertIn("pre-release-registry-preflight-${{ steps.authority.outputs.target_head }}-${{ github.run_id }}", text)
        self.assertIn("live_registry_version_preflight': True", text)
        self.assertIn("production_versions_must_be_available': True", text)
        self.assertRegex(
            text,
            r"(?s)actions/setup-node@820762786026740c76f36085b0efc47a31fe5020.*?node-version: '22'.*?Probe live registry version availability",
        )

    def test_external_actions_are_immutable_full_sha_pins(self) -> None:
        text = self.text
        refs = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", text)
        self.assertTrue(refs)
        external = [ref for ref in refs if not ref.startswith("./")]
        self.assertTrue(external)

        expected = {
            "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
            "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",
            "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
            "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
        }
        observed: dict[str, set[str]] = {}
        for ref in external:
            self.assertRegex(
                ref,
                r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$",
                ref,
            )
            action, sha = ref.rsplit("@", 1)
            observed.setdefault(action, set()).add(sha)

        self.assertEqual(set(observed), set(expected))
        for action, sha in expected.items():
            self.assertEqual(observed[action], {sha}, action)

        joined = "\n".join(external)
        for mutable in ("@v4", "@v5", "@release/v1", "@main", "@master"):
            self.assertNotIn(mutable, joined)
        self.assertIn("immutable_external_action_pins': True", text)

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

        self.assertIn("pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33", text)
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
        ledger = (ROOT / "tools" / "build_pre_release_publication_attempt_ledger.py").read_text(encoding="utf-8")
        self.assertIn("build_pre_release_publication_attempt_ledger.py", text)
        self.assertIn("pre-release-publication-attempt-ledger-${{ env.TARGET_HEAD }}", text)
        self.assertIn('"canonical_readiness_mutated": False', ledger)
        self.assertIn('"registry_receipts_admitted": False', ledger)
        self.assertIn("PUBLIC_VISIBILITY_EVIDENCE_ONLY_NOT_CANONICAL_REGISTRY_RECEIPT_ADMISSION", ledger)
        self.assertIn("separate exact-head reviewed change", ledger)
        self.assertNotRegex(text, r"(?m)^\s*git\s+(commit|push)\b")


if __name__ == "__main__":
    unittest.main()
