from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_setup_host_reference import certify


class PythonSetupHostReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_inventory_and_ownership_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["routes"]["route_count"], 21)
        self.assertEqual(len(self.report["routes"]["ownership"]), 21)
        self.assertEqual(len(self.report["routes"]["route_sha256"]), 64)

    def test_setup_dry_run_apply_doctor_and_repair_are_isolated(self) -> None:
        setup = self.report["bootstrap_setup"]
        self.assertTrue(setup["dry_run"]["ok"])
        self.assertTrue(setup["dry_run"]["dry_run"])
        self.assertFalse(setup["dry_run"]["mutated_target"])
        self.assertEqual(setup["dry_run"]["detected_hosts"], ["codex"])
        self.assertEqual(setup["dry_run"]["mental_model"], ["setup", "status", "run", "prove"])
        self.assertTrue(setup["applied"]["ok"])
        self.assertEqual(setup["applied"]["host_transaction_count"], 1)
        self.assertTrue(setup["applied"]["config_exists"])
        self.assertTrue(setup["doctor"]["ok"])
        self.assertEqual(setup["doctor"]["runtime_state"], "PRE_RELEASE_INSTALLED")
        self.assertTrue(setup["repair"]["diagnosed_action"])
        self.assertTrue(setup["repair"]["apply_ok"])
        self.assertTrue(setup["compat_install_default_dry_run"])

    def test_upgrade_wrap_init_and_uninstall_guards_are_frozen(self) -> None:
        setup = self.report["bootstrap_setup"]
        self.assertFalse(setup["upgrade"]["changed"])
        self.assertEqual(setup["upgrade"]["reason"], "version-locked-until-owner-authorization")
        self.assertEqual(setup["upgrade"]["invalid_target_exit"], 4)
        self.assertTrue(setup["wrapper"]["created"])
        self.assertEqual(setup["wrapper"]["unknown_host_exit"], 4)
        self.assertTrue(setup["init"]["session_persisted"])
        self.assertEqual(setup["uninstall_not_installed"]["reason"], "not-installed")

    def test_host_detection_and_negotiation_are_fixture_backed(self) -> None:
        hosts = self.report["host_detection"]
        self.assertGreater(hosts["known_host_count"], 20)
        self.assertEqual(len(hosts["known_host_sha256"]), 64)
        self.assertTrue(hosts["implicit_codex_equal"])
        self.assertEqual(hosts["codex"]["host"], "codex")
        self.assertEqual(hosts["claude_code"]["host"], "claude-code")
        self.assertTrue(hosts["detected_selected"]["codex_project_markers"])
        self.assertTrue(hosts["detected_selected"]["claude_user_markers"])
        self.assertEqual(hosts["capability_schema_keys"], ["coverage", "hosts", "platform"])
        self.assertEqual(hosts["capability_registry"]["host_count"], hosts["known_host_count"])
        self.assertEqual(
            hosts["capability_registry"]["claim_boundary"],
            "registry coverage is implementation coverage, not live host certification",
        )
        self.assertGreater(hosts["capability_registry"]["controlled_hosts"], 0)
        self.assertGreater(hosts["capability_registry"]["verified_hosts"], 0)

    def test_competitive_host_install_verify_and_rollback_are_reversible(self) -> None:
        fabric = self.report["fabric_install"]
        self.assertEqual(fabric["dry_run"]["status"], "dry-run")
        self.assertFalse(fabric["dry_run"]["mutated_target"])
        self.assertEqual(fabric["apply"]["status"], "applied")
        self.assertTrue(fabric["apply"]["verification_ok"])
        self.assertEqual(fabric["transactions"], {"count": 1, "status": "applied"})
        self.assertEqual(fabric["rollback"]["status"], "rolled-back")
        self.assertEqual(fabric["rollback"]["durable_status"], "rolled-back")
        self.assertEqual(fabric["rollback"]["idempotent_status"], "rolled-back")
        self.assertEqual(fabric["post_rollback_verify"]["exit"], 3)
        self.assertEqual(fabric["negative"]["missing_rollback_exit"], 4)
        self.assertEqual(fabric["negative"]["unsupported_host_exit"], 4)

    def test_update_install_checksum_and_rollback_are_frozen(self) -> None:
        updates = self.report["updates"]
        self.assertTrue(updates["first"]["ok"])
        self.assertTrue(updates["second"]["ok"])
        self.assertEqual(updates["second"]["previous"], updates["first"]["installed_sha256"])
        self.assertTrue(updates["rollback"]["ok"])
        self.assertTrue(updates["rollback"]["exact_restore"])
        self.assertTrue(updates["target_under_temp_root"])
        self.assertEqual(updates["missing_backup"]["exit"], 3)
        self.assertEqual(updates["malformed_artifact"], {"exit": 4, "error_type": "TypeError"})
        self.assertEqual(updates["bad_checksum"]["exit"], 3)
        self.assertTrue(updates["bad_checksum"]["detail_has_checksum_mismatch"])

    def test_certification_never_targets_developer_machine(self) -> None:
        safety = self.report["safety"]
        self.assertEqual(safety["certification_home"], "temporary")
        self.assertEqual(safety["certification_project"], "temporary")
        self.assertFalse(safety["external_network"])
        self.assertFalse(safety["developer_home_mutation"])
        self.assertEqual(safety["destructive_host_install"], "temporary project only")
        self.assertEqual(safety["update_install_root"], "temporary state only")
        self.assertEqual(self.report["exit_policy"]["success"], 0)
        self.assertEqual(self.report["exit_policy"]["application_or_malformed_input"], 4)


if __name__ == "__main__":
    unittest.main()
