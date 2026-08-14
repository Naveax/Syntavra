from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_sandbox_security_reference import certify


class PythonSandboxSecurityReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_sandbox_route_inventory_is_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["routes"]["route_count"], 5)
        self.assertEqual(
            self.report["routes"]["routes"],
            [
                "run sandbox-run",
                "run sandbox-status",
                "sandbox backends",
                "sandbox execute",
                "sandbox plan",
            ],
        )
        self.assertEqual(len(self.report["routes"]["ownership"]), 5)

    def test_direct_sandbox_degraded_behavior_and_evidence_are_explicit(self) -> None:
        direct = self.report["direct"]
        self.assertTrue(direct["backends"]["local_restricted_available"])
        self.assertEqual(direct["plan"]["backend"], "local-restricted")
        self.assertEqual(
            direct["plan"]["degraded_reasons"],
            ["network-isolation-unavailable", "filesystem-overlay-unavailable"],
        )
        self.assertFalse(direct["plan"]["guarantees"]["network_isolated"])
        self.assertFalse(direct["plan"]["guarantees"]["filesystem_isolated"])
        self.assertTrue(direct["plan"]["guarantees"]["secret_filtered"])
        self.assertEqual(
            direct["execute"]["environment"],
            {"sandbox": "1", "secret": None, "workspace": True},
        )
        self.assertTrue(direct["execute"]["evidence_shape"])
        self.assertEqual(direct["child_exit_passthrough"], {"public_exit": 7, "receipt_exit_code": 7})

    def test_direct_policy_and_filesystem_denials_are_fail_closed(self) -> None:
        direct = self.report["direct"]
        self.assertEqual(direct["strict_denial"]["exit"], 4)
        self.assertEqual(direct["strict_denial"]["error_type"], "SandboxError")
        self.assertEqual(direct["filesystem"]["blocked_write_reason"], "path is not writable by policy")
        self.assertEqual(direct["filesystem"]["escape_read_reason"], "path escapes project")

    def test_platform_receipt_environment_and_failure_semantics_are_frozen(self) -> None:
        platform = self.report["platform"]
        self.assertTrue(platform["status"]["fail_closed"])
        self.assertEqual(
            platform["status"]["strict_ready"],
            bool(platform["status"]["backend_available"] and not platform["status"]["unsupported"]),
        )
        run = platform["allowed_run"]
        self.assertEqual(run["public_exit"], 0)
        self.assertTrue(run["ok"])
        self.assertTrue(run["receipt_id_shape"])
        self.assertEqual(run["environment"], {"sandbox": "1", "secret": None, "workspace": True})
        self.assertTrue(run["secret_key_absent"])
        self.assertEqual(run["durable_receipt_count"], 1)
        self.assertTrue(run["durable_receipt_exact"])
        self.assertEqual(platform["child_failure"]["public_exit"], 3)
        self.assertEqual(platform["child_failure"]["receipt_exit_code"], 7)
        self.assertFalse(platform["child_failure"]["ok"])

    def test_platform_adversarial_policy_denials_are_explicit(self) -> None:
        denials = self.report["platform"]["denials"]
        self.assertEqual(denials["cwd_escape"]["exit"], 4)
        self.assertEqual(denials["writable_escape"]["exit"], 4)
        self.assertEqual(denials["malformed_argv"]["exit"], 4)
        self.assertEqual(denials["strict_native_reason"], "required native sandbox controls unavailable")
        self.assertEqual(denials["secret_environment_reason"], "secret-like environment key is not agent-visible")

    def test_command_authorization_boundary_and_exit_policy_are_not_overclaimed(self) -> None:
        self.assertEqual(self.report["command_policy"]["classification"], "content-agnostic")
        self.assertIn("capability authorization", self.report["command_policy"]["authorization_boundary"])
        self.assertEqual(
            self.report["exit_policy"],
            {
                "direct_success": 0,
                "direct_child_exit": "passthrough",
                "direct_timeout": 124,
                "direct_application_error": 4,
                "platform_success": 0,
                "platform_receipt_failure": 3,
                "platform_application_error": 4,
                "argparse_error": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
