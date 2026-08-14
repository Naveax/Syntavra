from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_mcp_integration_reference import certify


class PythonMCPIntegrationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_cli_inventory_is_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["routes"]["route_count"], 3)
        self.assertEqual(len(self.report["routes"]["ownership"]), 3)
        self.assertEqual(len(self.report["routes"]["route_sha256"]), 64)

    def test_mcp_tool_catalog_and_profile_counts_are_deterministic(self) -> None:
        inventory = self.report["tool_inventory"]
        self.assertGreater(inventory["count"], 30)
        self.assertEqual(len(inventory["sha256"]), 64)
        self.assertEqual(len(inventory["name_sha256"]), 64)
        self.assertEqual(inventory["profile_counts"], {"minimal": 10, "balanced": 36})
        for name in ("syntavra.status", "syntavra.context.evaluate", "syntavra.sandbox.execute", "syntavra.session.open"):
            self.assertIn(name, inventory["selected_schemas"])
            self.assertEqual(inventory["selected_schemas"][name]["input_schema_type"], "object")

    def test_stdio_jsonrpc_lifecycle_and_errors_are_frozen(self) -> None:
        stdio = self.report["stdio"]
        self.assertEqual(stdio["exit"], 0)
        self.assertEqual(stdio["response_count"], 10)
        self.assertEqual(stdio["notification_response_count"], 0)
        self.assertEqual(stdio["initialize"]["protocol_version"], "2025-06-18")
        self.assertEqual(stdio["initialize"]["server_info"], {"name": "syntavra", "version": "0.0.1"})
        self.assertEqual(stdio["ping_result"], {})
        self.assertEqual(stdio["tools_list"]["count"], 10)
        self.assertEqual(stdio["status_call"]["profile"], "minimal")
        self.assertEqual(stdio["status_call"]["risk"], "read-or-plan")
        self.assertTrue(stdio["status_call"]["route_receipt_shape"])
        self.assertEqual(
            stdio["denied_call"],
            {"code": -32001, "reason": "tool-not-exposed-by-active-profile"},
        )
        self.assertEqual(stdio["unknown_method"], {"code": -32601, "message": "Method not found"})
        self.assertEqual(stdio["parse_error"], {"code": -32700, "message": "Parse error"})
        self.assertEqual(stdio["invalid_request"], {"code": -32600, "message": "Invalid Request"})
        self.assertEqual(stdio["invalid_parameters"], {"code": -32602, "message": "Invalid params"})
        self.assertEqual(stdio["post_error_ping_result"], {})

    def test_integration_registry_and_family_filters_are_deterministic(self) -> None:
        integrations = self.report["integrations"]
        self.assertGreater(integrations["count"], 10)
        self.assertEqual(sum(integrations["family_counts"].values()), integrations["count"])
        self.assertEqual(integrations["family_filters"], integrations["family_counts"])
        self.assertEqual(len(integrations["sha256"]), 64)
        self.assertTrue(integrations["coverage"]["ok"])
        self.assertEqual(integrations["coverage"]["providers"], integrations["family_counts"]["provider"])
        self.assertEqual(integrations["coverage"]["frameworks"], integrations["family_counts"]["framework"])
        self.assertEqual(integrations["coverage"]["hosts"], integrations["family_counts"]["host"])
        self.assertIn("ok", integrations["platform_adapters"])
        self.assertIn("ok", integrations["proxy_presets"])

    def test_route_policy_freezes_profile_authorization_and_unknown_tool_behavior(self) -> None:
        route = self.report["route_policy"]
        self.assertTrue(route["minimal_read"]["allowed"])
        self.assertEqual(route["minimal_read"]["reason"], "policy-allowed")
        self.assertEqual(route["minimal_read"]["category"], "read")
        self.assertFalse(route["unsafe_execute_denied"]["allowed"])
        self.assertEqual(route["unsafe_execute_denied"]["reason"], "sandbox-required")
        self.assertFalse(route["balanced_execute_no_auth"]["allowed"])
        self.assertEqual(route["balanced_execute_no_auth"]["reason"], "explicit-user-authorization-required")
        self.assertTrue(route["balanced_execute_allowed"]["allowed"])
        self.assertEqual(route["balanced_execute_allowed"]["category"], "execute")
        self.assertFalse(route["unknown_tool"]["allowed"])
        self.assertEqual(route["unknown_tool"]["reason"], "unknown-tool-fail-closed")

    def test_certification_is_offline_and_error_codes_are_explicit(self) -> None:
        self.assertEqual(
            self.report["network_boundary"],
            "offline stdio JSON-RPC and local registry fixtures only; no MCP socket/network transport or external service",
        )
        self.assertEqual(self.report["jsonrpc_errors"]["parse_error"], -32700)
        self.assertEqual(self.report["jsonrpc_errors"]["invalid_request"], -32600)
        self.assertEqual(self.report["jsonrpc_errors"]["method_not_found"], -32601)
        self.assertEqual(self.report["jsonrpc_errors"]["invalid_parameters"], -32602)
        self.assertEqual(self.report["jsonrpc_errors"]["policy_denied"], -32001)
        self.assertEqual(self.report["exit_policy"]["success"], 0)
        self.assertEqual(self.report["exit_policy"]["argparse_error"], 2)
        self.assertEqual(self.report["exit_policy"]["application_error"], 4)


if __name__ == "__main__":
    unittest.main()
