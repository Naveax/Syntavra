from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tools.certify_python_behavior_freeze import (
    _assert_no_full_route_copy,
    _expected_hash,
    _load_and_verify_freeze,
    _verify_static_authorities,
)


class PythonBehaviorFreezeArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.contract, cls.freeze_sha = _load_and_verify_freeze(cls.repo)

    def test_identity_and_companion_hash_are_valid(self) -> None:
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["family"], "python-behavior-freeze")
        self.assertEqual(self.contract["phase"], "S")
        self.assertEqual(len(self.freeze_sha), 64)

    def test_q_and_r_static_authorities_match_frozen_hashes(self) -> None:
        catalog, catalog_file_sha, suite_contract_sha = _verify_static_authorities(
            self.repo,
            self.contract,
        )
        self.assertEqual(len(catalog["families"]), 14)
        self.assertEqual(catalog_file_sha, self.contract["fixture_catalog"]["expected_file_sha256"])
        self.assertEqual(
            suite_contract_sha,
            self.contract["reference_suite"]["expected_contract_sha256"],
        )

    def test_static_freeze_is_not_a_second_245_route_authority(self) -> None:
        _assert_no_full_route_copy(self.contract)
        self.assertEqual(self.contract["public_surface"]["expected_route_count"], 245)
        self.assertEqual(
            self.contract["public_surface"]["authority"],
            "tools/report_missing_native_public_routes.py",
        )
        self.assertTrue(self.contract["public_surface"]["duplicate_route_list_forbidden"])
        self.assertFalse(self.contract["policy"]["static_route_manifest_copy"])
        self.assertTrue(self.contract["policy"]["full_route_manifest_in_certification_artifact"])

    def test_bootstrap_allows_null_derived_hashes_but_strict_mode_does_not(self) -> None:
        bootstrap = copy.deepcopy(self.contract)
        bootstrap["strict"] = False
        for key in bootstrap["derived_freeze"]:
            bootstrap["derived_freeze"][key] = None
            _expected_hash(bootstrap, key, "0" * 64)

        strict = copy.deepcopy(bootstrap)
        strict["strict"] = True
        for key in strict["derived_freeze"]:
            with self.subTest(key=key):
                with self.assertRaisesRegex(AssertionError, "strict behavior freeze is missing derived hash"):
                    _expected_hash(strict, key, "0" * 64)

    def test_current_strict_contract_has_complete_derived_hashes(self) -> None:
        if not self.contract["strict"]:
            self.skipTest("bootstrap freeze has not been promoted to strict yet")
        self.assertEqual(self.contract["claim"], "PYTHON_REFERENCE_BEHAVIOR_FROZEN")
        for key, value in self.contract["derived_freeze"].items():
            with self.subTest(key=key):
                self.assertIsInstance(value, str)
                self.assertEqual(len(value), 64)
                _expected_hash(self.contract, key, value)

    def test_strict_hash_mismatch_fails_closed(self) -> None:
        strict = copy.deepcopy(self.contract)
        strict["strict"] = True
        key = "expected_family_schema_sha256"
        strict["derived_freeze"][key] = "1" * 64
        with self.assertRaisesRegex(AssertionError, "derived behavior freeze drift"):
            _expected_hash(strict, key, "2" * 64)

    def test_rust_promotion_remains_blocked(self) -> None:
        policy = self.contract["policy"]
        self.assertFalse(policy["rust_python_required_for_fixture_consumption"])
        self.assertFalse(policy["rust_native_promotion_credit"])
        self.assertEqual(policy["frozen_rust_native_count"], 174)


if __name__ == "__main__":
    unittest.main()
