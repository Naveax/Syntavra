from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_provider_proxy_reference import certify


class PythonProviderProxyReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_provider_proxy_family_inventory_is_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        routes = self.report["routes"]
        self.assertEqual(routes["route_count"], 15)
        self.assertEqual(len(routes["route_sha256"]), 64)
        self.assertEqual(len(routes["ownership"]), 15)

    def test_provider_gateway_and_configuration_contract(self) -> None:
        gateway = self.report["provider_gateway"]
        self.assertEqual(
            gateway["canonical_providers"],
            ["anthropic", "gemini", "openai", "openai-compatible"],
        )
        self.assertEqual(gateway["azure_openai_canonical"], "openai")
        self.assertEqual(gateway["unsupported"]["exit"], 4)
        self.assertEqual(gateway["credential_payload_rejection"]["exit"], 4)
        self.assertTrue(gateway["prepared_request_has_prompt_cache_key"])
        self.assertTrue(gateway["capture_replay_stored"])
        self.assertTrue(gateway["replay_exact_fixture"])
        config = gateway["proxy_config"]
        self.assertEqual(config["stream_mode"], "commit-before-forward")
        self.assertEqual(config["timeout_seconds"], 180.0)
        self.assertEqual(config["control_token_env"], "TEST_CONTROL_TOKEN")

    def test_provider_selection_and_helper_contract(self) -> None:
        helpers = self.report["helpers"]
        self.assertFalse(helpers["gateway_plan"]["agent_environment_contains_secret"])
        self.assertEqual(helpers["gateway_plan"]["transport_visibility"], "gateway-process-only")
        self.assertEqual(helpers["proxy_plan"]["stream_mode"], "commit-before-forward")
        self.assertEqual(helpers["proxy_plan"]["credential_policy"], "transport-only")
        self.assertEqual(helpers["adaptive_route"]["provider"], "anthropic")
        self.assertEqual(helpers["adaptive_route"]["model"], "subscription-model")
        self.assertEqual(helpers["adaptive_route"]["complexity"], "reasoning")
        self.assertEqual(helpers["provider_pool"]["selected_account"], "primary")
        self.assertEqual(helpers["provider_pool"]["raw_secret_rejection"]["exit"], 4)

    def test_proxy_transport_is_fail_closed_and_has_no_automatic_retry(self) -> None:
        transport = self.report["transport"]
        self.assertEqual(transport["retry_policy"], "no-automatic-retry")
        self.assertEqual(transport["normal"]["status"], 200)
        self.assertEqual(transport["normal"]["upstream_attempts"], 1)
        self.assertFalse(transport["normal"]["client_authorization_forwarded"])
        self.assertFalse(transport["normal"]["response_authorization_forwarded"])
        self.assertTrue(transport["normal"]["internal_request_id"])
        self.assertTrue(transport["normal"]["evidence_header"])
        self.assertEqual(transport["replay"]["replay_header"], "hit")
        self.assertEqual(transport["replay"]["upstream_attempts"], 0)
        self.assertEqual(transport["http_error"]["status"], 429)
        self.assertEqual(transport["http_error"]["upstream_attempts"], 1)
        self.assertEqual(transport["http_error"]["retry_after"], "7")
        self.assertTrue(transport["http_error"]["evidence_header"])
        self.assertEqual(transport["timeout"]["status"], 502)
        self.assertEqual(transport["timeout"]["body"], {"error": "TimeoutError", "detail": "upstream request failed"})
        self.assertEqual(transport["timeout"]["upstream_attempts"], 1)
        self.assertEqual(transport["missing_credential"]["status"], 502)
        self.assertEqual(transport["missing_credential"]["upstream_attempts"], 0)
        self.assertEqual(transport["control"]["unauthorized_status"], 401)

    def test_certification_is_offline_and_exit_policy_is_explicit(self) -> None:
        self.assertEqual(
            self.report["network_boundary"],
            "localhost-only deterministic HTTP fixture; no live provider or SaaS endpoint",
        )
        self.assertEqual(
            self.report["exit_policy"],
            {
                "success": 0,
                "python_application_error": 4,
                "provider_replay_miss": 4,
                "provider_verify_failure": 3,
                "argparse_error": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
