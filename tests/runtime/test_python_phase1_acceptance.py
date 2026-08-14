from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path

from tools.certify_python_phase1_acceptance import (
    _assert_no_full_route_copy,
    _expected_hash,
    _load_contract,
)


class PythonPhase1AcceptanceArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.contract, cls.contract_sha = _load_contract(cls.repo)

    def test_identity_and_companion_hash_are_valid(self) -> None:
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["family"], "python-phase1-acceptance")
        self.assertEqual(self.contract["phase"], "final-python-phase1")
        self.assertEqual(len(self.contract_sha), 64)

    def test_strict_s_freeze_is_the_only_behavior_authority(self) -> None:
        cfg = self.contract["behavior_freeze"]
        freeze = self.repo / cfg["path"]
        observed = hashlib.sha256(freeze.read_bytes()).hexdigest()
        self.assertEqual(observed, cfg["expected_sha256"])
        self.assertEqual(cfg["expected_claim"], "PYTHON_REFERENCE_BEHAVIOR_FROZEN")
        self.assertEqual(cfg["expected_family_count"], 14)
        self.assertEqual(cfg["expected_suite_certifiers"], 15)

    def test_route_binding_is_derived_not_a_second_route_authority(self) -> None:
        cfg = self.contract["route_binding"]
        self.assertEqual(cfg["authority"], "tools/report_missing_native_public_routes.py")
        self.assertEqual(cfg["expected_route_count"], 245)
        self.assertEqual(cfg["expected_family_explicit_route_count"], 147)
        self.assertEqual(cfg["expected_core_legacy_route_count"], 98)
        self.assertTrue(cfg["duplicate_route_list_forbidden"])
        self.assertTrue(cfg["unbound_routes_forbidden"])
        self.assertTrue(cfg["overlapping_primary_family_routes_forbidden"])
        _assert_no_full_route_copy(self.contract)

    def test_b_semantics_vocabulary_is_complete_and_exact(self) -> None:
        self.assertEqual(
            self.contract["required_route_semantics"],
            [
                "success_exit_code",
                "domain_application_error_policy",
                "argument_parser_error_policy",
                "stdout_format_policy",
                "stderr_format_policy",
                "json_envelope_schema_policy",
                "ordering_guarantee",
                "filesystem_state_side_effect_policy",
                "idempotency_behavior",
                "missing_input_behavior",
                "malformed_input_behavior",
                "unsupported_operation_behavior",
            ],
        )

    def test_bootstrap_and_strict_hash_lifecycle_fail_closed(self) -> None:
        bootstrap = copy.deepcopy(self.contract)
        bootstrap["strict"] = False
        bootstrap["derived_freeze"]["expected_route_semantics_sha256"] = None
        _expected_hash(bootstrap, "0" * 64)

        strict_missing = copy.deepcopy(bootstrap)
        strict_missing["strict"] = True
        with self.assertRaisesRegex(AssertionError, "strict Phase 1 acceptance is missing"):
            _expected_hash(strict_missing, "0" * 64)

        strict_drift = copy.deepcopy(bootstrap)
        strict_drift["strict"] = True
        strict_drift["derived_freeze"]["expected_route_semantics_sha256"] = "1" * 64
        with self.assertRaisesRegex(AssertionError, "route semantics drift"):
            _expected_hash(strict_drift, "2" * 64)

    def test_policy_is_fail_closed_and_rust_remains_blocked(self) -> None:
        policy = self.contract["policy"]
        self.assertTrue(policy["no_implicit_ordering"])
        self.assertTrue(policy["no_implicit_idempotency"])
        self.assertEqual(policy["unknown_public_selector_exit"], 2)
        self.assertEqual(policy["parser_valid_route_owner_count"], 1)
        self.assertFalse(policy["generic_runtime_fallthrough_reachable"])
        self.assertEqual(policy["public_application_failure_default_exit"], 4)
        self.assertFalse(policy["rust_native_promotion_credit"])
        self.assertEqual(policy["frozen_rust_native_count"], 174)


if __name__ == "__main__":
    unittest.main()
