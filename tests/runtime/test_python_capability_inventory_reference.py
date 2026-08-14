from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_capability_inventory_reference import certify


class PythonCapabilityInventoryReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_canonical_inventory_is_parser_derived_and_complete(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        inventory = self.report["inventory"]
        self.assertEqual(inventory["route_count"], 245)
        self.assertEqual(inventory["duplicate_routes"], 0)
        self.assertEqual(inventory["namespace_dest_collisions"], 0)
        self.assertEqual(inventory["unknown_source_rows"], 0)
        self.assertEqual(inventory["unowned_routes"], 0)
        self.assertEqual(
            inventory["capability_routes"],
            ["run capability-decide", "run capability-issue", "run capability-verify"],
        )
        self.assertEqual(
            set(inventory["capability_owners"].values()),
            {"syntavra_runtime.prerelease_cli.main"},
        )

    def test_route_ownership_metadata_shape_is_frozen(self) -> None:
        inventory = self.report["inventory"]
        self.assertEqual(inventory["manifest_record_keys"], ["route", "sources"])
        self.assertEqual(
            inventory["execution_record_keys"],
            [
                "entrypoint",
                "entrypoints",
                "parser_error_exit",
                "parser_owned",
                "route",
                "sources",
                "success_exit",
                "unknown_sources",
            ],
        )
        self.assertEqual(len(inventory["command_paths_sha256"]), 64)
        self.assertEqual(len(inventory["ownership_sha256"]), 64)

    def test_capability_prefix_classification_and_fail_closed_unknown(self) -> None:
        decisions = self.report["capability"]["decisions"]
        self.assertTrue(decisions["read"]["allowed"])
        self.assertEqual(decisions["read"]["category"], "read")
        self.assertEqual(decisions["write_authorization_required"]["reason"], "authorization-required")
        self.assertTrue(decisions["write_allowed"]["allowed"])
        self.assertEqual(decisions["write_allowed"]["category"], "write")
        self.assertEqual(decisions["execute_sandbox_required"]["reason"], "sandbox-required")
        self.assertTrue(decisions["execute_allowed"]["allowed"])
        self.assertEqual(decisions["execute_allowed"]["category"], "execute")
        self.assertEqual(decisions["destructive_denied"]["reason"], "destructive-command-denied")
        self.assertEqual(decisions["outside_workspace_denied"]["reason"], "resource-outside-workspace")
        self.assertEqual(decisions["network_denied"]["reason"], "network-host-not-allowlisted")
        self.assertFalse(decisions["unknown_tool_denied"]["allowed"])
        self.assertEqual(decisions["unknown_tool_denied"]["category"], "unknown")
        self.assertEqual(decisions["unknown_tool_denied"]["reason"], "unknown-tool-fail-closed")

    def test_capability_vocabulary_is_stable(self) -> None:
        capability = self.report["capability"]
        self.assertEqual(capability["category_vocabulary"], ["execute", "network", "read", "unknown", "write"])
        self.assertEqual(
            capability["decision_reason_vocabulary"],
            [
                "authorization-required",
                "destructive-command-denied",
                "network-host-not-allowlisted",
                "policy-allowed",
                "resource-outside-workspace",
                "sandbox-required",
                "unknown-tool-fail-closed",
            ],
        )
        self.assertEqual(
            capability["requirement_vocabulary"],
            ["exact-evidence", "explicit-user-authorization", "sandbox", "signed-capability"],
        )
        self.assertEqual(
            capability["verify"]["reason_vocabulary"],
            ["already-consumed", "binding-mismatch", "expired", "invalid-signature", "malformed-token", "verified"],
        )

    def test_single_use_token_binding_expiry_and_durable_consumption_are_frozen(self) -> None:
        capability = self.report["capability"]
        self.assertTrue(capability["issue"]["single_use"])
        self.assertEqual(capability["issue"]["token_shape"], "base64url-json.hmac-sha256")
        self.assertEqual(capability["issue"]["top_level_keys"], ["ok", "single_use", "token"])
        self.assertEqual(capability["issue"]["capability"]["ttl_seconds"], 300)
        self.assertEqual(capability["issue"]["capability"]["permissions"], ["evidence", "write"])

        self.assertTrue(capability["verify"]["first"]["ok"])
        self.assertEqual(capability["verify"]["first"]["reason"], "verified")
        self.assertEqual(capability["verify"]["first"]["exit"], 0)
        self.assertEqual(capability["verify"]["replay"]["reason"], "already-consumed")
        self.assertEqual(capability["verify"]["binding_mismatch"]["reason"], "binding-mismatch")
        self.assertEqual(capability["verify"]["expired"]["reason"], "expired")
        self.assertEqual(capability["verify"]["malformed"], {
            "exit": 3,
            "ok": False,
            "reason": "malformed-token",
            "top_level_keys": ["ok", "reason"],
        })
        self.assertEqual(capability["verify"]["invalid_signature"], {
            "exit": 3,
            "ok": False,
            "reason": "invalid-signature",
            "top_level_keys": ["ok", "reason"],
        })
        self.assertEqual(
            capability["durable_side_effects"],
            {
                "signing_key_bytes": 32,
                "consumed_rows": 1,
                "consumed_nonce_matches_issued_token": True,
                "store": "sqlite",
            },
        )

    def test_exit_policy_and_argument_parser_error_are_explicit(self) -> None:
        self.assertEqual(
            self.report["exit_policy"],
            {"success": 0, "verification_failure": 3, "argument_parser_error": 2},
        )
        self.assertEqual(self.report["capability"]["parser_error"]["exit"], 2)


if __name__ == "__main__":
    unittest.main()
