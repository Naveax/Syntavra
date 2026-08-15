from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_pre_release_publish_credentials import (
    CredentialPreflightError,
    JsonResponse,
    build_report,
    verify_npm_scope_authorization,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pre-release.yml"
CHECKER = ROOT / "tools" / "check_pre_release_publish_credentials.py"
HEAD = "b" * 40


def npm_scope(*, verified: bool = True) -> dict:
    return {
        "verified": verified,
        "scope": "@syntavra",
        "scope_kind": "organization",
        "identity": "publisher-user",
        "organization_role": "member" if verified else None,
        "publication_performed": False,
    }


class PublishCredentialReportTests(unittest.TestCase):
    def test_all_zero_write_auth_checks_make_report_ready(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            npm_scope=npm_scope(),
            crates_token_present=True,
            pypi={"verified": True, "publication_performed": False},
            marketplace={"verified": True, "publication_performed": False},
        )
        self.assertTrue(report["publish_auth_ready"])
        self.assertEqual(report["claim"], "ZERO_WRITE_PUBLISH_AUTH_READY")
        self.assertTrue(report["npm"]["scope_publish_rights_verified"])
        self.assertFalse(report["publication_performed"])
        self.assertFalse(report["credential_values_exposed"])

    def test_missing_npm_auth_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=False,
            npm_identity="",
            npm_scope=npm_scope(verified=False),
            crates_token_present=True,
            pypi={"verified": True},
            marketplace={"verified": True},
        )
        self.assertFalse(report["publish_auth_ready"])
        self.assertEqual(report["claim"], "ZERO_WRITE_PUBLISH_AUTH_INCOMPLETE")

    def test_missing_npm_scope_authorization_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            npm_scope=npm_scope(verified=False),
            crates_token_present=True,
            pypi={"verified": True},
            marketplace={"verified": True},
        )
        self.assertFalse(report["publish_auth_ready"])
        self.assertFalse(report["npm"]["scope_publish_rights_verified"])

    def test_missing_crates_bootstrap_token_fails_closed(self) -> None:
        report = build_report(
            exact_head=HEAD,
            npm_authenticated=True,
            npm_identity="publisher-user",
            npm_scope=npm_scope(),
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
            npm_scope=npm_scope(),
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
                npm_scope=npm_scope(),
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
                npm_scope=npm_scope(),
                crates_token_present=True,
                pypi={"verified": True},
                marketplace={"verified": True},
            )


class NpmScopeAuthorizationTests(unittest.TestCase):
    def test_matching_user_scope_requires_no_org_request(self) -> None:
        with patch("tools.check_pre_release_publish_credentials._request_json") as request:
            result = verify_npm_scope_authorization("syntavra")
        request.assert_not_called()
        self.assertTrue(result["verified"])
        self.assertEqual(result["scope_kind"], "user")
        self.assertIsNone(result["organization_role"])
        self.assertFalse(result["publication_performed"])

    def test_org_member_role_is_verified_with_read_only_registry_request(self) -> None:
        with (
            patch.dict("tools.check_pre_release_publish_credentials.os.environ", {"NODE_AUTH_TOKEN": "secret-token"}, clear=False),
            patch(
                "tools.check_pre_release_publish_credentials._request_json",
                return_value=JsonResponse(status=200, value={"publisher-user": "member"}),
            ) as request,
        ):
            result = verify_npm_scope_authorization("publisher-user")
        self.assertTrue(result["verified"])
        self.assertEqual(result["scope_kind"], "organization")
        self.assertEqual(result["organization_role"], "member")
        args, kwargs = request.call_args
        self.assertEqual(args[0], "https://registry.npmjs.org/-/org/syntavra/user")
        self.assertEqual(kwargs.get("headers", {}).get("Authorization"), "Bearer secret-token")

    def test_org_outsider_fails_closed(self) -> None:
        with (
            patch.dict("tools.check_pre_release_publish_credentials.os.environ", {"NODE_AUTH_TOKEN": "secret-token"}, clear=False),
            patch(
                "tools.check_pre_release_publish_credentials._request_json",
                return_value=JsonResponse(status=200, value={"someone-else": "owner"}),
            ),
        ):
            with self.assertRaisesRegex(CredentialPreflightError, "not a publish-capable member"):
                verify_npm_scope_authorization("publisher-user")

    def test_non_publish_role_fails_closed(self) -> None:
        with (
            patch.dict("tools.check_pre_release_publish_credentials.os.environ", {"NODE_AUTH_TOKEN": "secret-token"}, clear=False),
            patch(
                "tools.check_pre_release_publish_credentials._request_json",
                return_value=JsonResponse(status=200, value={"publisher-user": "read-only"}),
            ),
        ):
            with self.assertRaisesRegex(CredentialPreflightError, "not a publish-capable member"):
                verify_npm_scope_authorization("publisher-user")


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
        self.assertIn('"credential_persisted": False', text)

    def test_marketplace_exchange_is_zero_write(self) -> None:
        text = self.checker
        self.assertIn("marketplace.visualstudio.com", text)
        self.assertIn("/_apis/gallery/token", text)
        self.assertIn("publisherName", text)
        self.assertNotIn("publish --oidc", text)

    def test_npm_scope_authorization_is_zero_write_and_exact(self) -> None:
        text = self.checker
        self.assertIn("/-/org/{NPM_SCOPE}/user", text)
        self.assertIn("NODE_AUTH_TOKEN", text)
        self.assertIn('"Authorization": f"Bearer {token}"', text)
        self.assertIn('"scope_publish_rights_verified"', text)
        self.assertNotIn("npm access grant", text)
        self.assertNotIn("npm org set", text)
        self.assertNotIn("npm team add", text)

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
            match = re.search(
                rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
                text,
            )
            self.assertIsNotNone(match, job)
            self.assertIn("credential-preflight", match.group("body"), job)

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
