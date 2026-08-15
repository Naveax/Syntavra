from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_pre_release_publish_credentials import build_report


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
CHECKER = ROOT / "tools" / "check_pre_release_publish_credentials.py"
HEAD = "b" * 40


class PublishCredentialReportTests(unittest.TestCase):
    def test_all_zero_write_auth_checks_make_report_ready(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            crates_token_present=True,
            pypi={"verified": True, "publication_performed": False},
            marketplace={"verified": True, "publication_performed": False},
        )
        self.assertTrue(report["publish_auth_ready"])
        self.assertEqual(report["claim"], "ZERO_WRITE_PUBLISH_AUTH_READY")
        self.assertFalse(report["publication_performed"])
        self.assertFalse(report["credential_values_exposed"])

    def test_missing_npm_auth_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=False,
            npm_identity="",
            crates_token_present=True,
            pypi={"verified": True},
            marketplace={"verified": True},
        )
        self.assertFalse(report["publish_auth_ready"])
        self.assertEqual(report["claim"], "ZERO_WRITE_PUBLISH_AUTH_INCOMPLETE")

    def test_missing_crates_bootstrap_token_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            crates_token_present=False,
            pypi={"verified": True},
            marketplace={"verified": True},
        )
        self.assertFalse(report["publish_auth_ready"])
        self.assertFalse(report["crates_io"]["remote_token_validation_performed"])

    def test_missing_oidc_binding_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            crates_token_present=True,
            pypi={"verified": False},
            marketplace={"verified": True},
        )
        self.assertFalse(report["publish_auth_ready"])

    def test_npm_identity_is_not_accepted_when_auth_claims_success_without_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "npm identity"):
            build_report(
                exact_head=HEAD,
                npm_authenticated=True,
                npm_identity="",
                crates_token_present=True,
                pypi={"verified": True},
                marketplace={"verified": True},
            )

    def test_exact_head_is_strict_hex(self) -> None:
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            build_report(
                exact_head=("b" * 39) + "z",
                npm_authenticated=True,
                npm_identity="publisher-user",
                crates_token_present=True,
                pypi={"verified": True},
                marketplace={"verified": True},
            )


class PublishCredentialSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = CHECKER.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_pypi_exchange_matches_trusted_publishing_flow(self) -> None:
        text = self.checker
        self.assertIn("/_/oidc/audience", text)
        self.assertIn("/_/oidc/mint-token", text)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_URL", text)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", text)
        self.assertIn("credential_persisted\": False", text)

    def test_marketplace_exchange_is_zero_write(self) -> None:
        text = self.checker
        self.assertIn("marketplace.visualstudio.com", text)
        self.assertIn("/_apis/gallery/token", text)
        self.assertIn("publisherName", text)
        self.assertNotIn("publish --oidc", text)

    def test_publish_jobs_depend_on_protected_credential_preflight(self) -> None:
        text = self.workflow
        self.assertIn("credential-preflight:", text)
        self.assertRegex(
            text,
            r"(?s)credential-preflight:.*?environment: pre-release.*?id-token: write",
        )
        self.assertIn("npm whoami", text)
        self.assertIn("check_pre_release_publish_credentials.py", text)
        for job in (
            "publish-pypi",
            "publish-npm-installer",
            "publish-npm-sdk",
            "publish-rust-production",
            "publish-vscode",
            "publish-legacy-native",
        ):
            block_start = text.index(f"  {job}:")
            next_job = text.find("\n  ", block_start + 3)
            block = text[block_start:] if next_job < 0 else text[block_start:next_job]
            self.assertIn("credential-preflight", block, job)

    def test_preflight_has_no_registry_write_command(self) -> None:
        text = self.workflow
        start = text.index("  credential-preflight:")
        end = text.index("\n  publish-pypi:", start)
        block = text[start:end]
        self.assertNotIn("npm publish", block)
        self.assertNotIn("cargo +1.82.0 publish", block)
        self.assertNotIn("gh-action-pypi-publish", block)
        self.assertNotIn("vsce publish", block)
        self.assertIn("publication_performed", self.checker)


if __name__ == "__main__":
    unittest.main()
