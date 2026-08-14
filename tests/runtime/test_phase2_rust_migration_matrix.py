from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools import report_phase2_rust_migration_matrix as matrix


class Phase2RustMigrationMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.contract_path = cls.repo / "contracts/engine/phase2-rust-migration-matrix-v1.json"
        cls.contract = json.loads(cls.contract_path.read_text(encoding="utf-8"))

    def test_contract_has_no_duplicate_route_authority(self) -> None:
        matrix._assert_no_route_identity_copy(self.contract)
        self.assertTrue(self.contract["policy"]["hardcoded_remaining_route_list_forbidden"])
        self.assertTrue(self.contract["policy"]["route_identity_authority_single_source"])
        self.assertEqual(
            self.contract["rust_baseline"]["remaining_authority"],
            "tools/report_missing_native_public_routes.py",
        )

    def test_frozen_entry_counts_are_245_174_71_with_150_rust_modules(self) -> None:
        self.assertEqual(self.contract["python_reference"]["expected_public_route_count"], 245)
        self.assertEqual(self.contract["rust_baseline"]["expected_rust_module_count"], 150)
        self.assertEqual(self.contract["rust_baseline"]["expected_promoted_native"], 174)
        self.assertEqual(self.contract["rust_baseline"]["expected_remaining"], 71)
        self.assertEqual(self.contract["rust_baseline"]["expected_remaining_owned"], 71)
        self.assertEqual(self.contract["rust_baseline"]["expected_unowned"], 0)
        self.assertEqual(self.contract["rust_baseline"]["atomic_promotion_target"], 245)

    def test_single_ownership_probe_covers_selector_and_lower_module_policy(self) -> None:
        rust = self.contract["rust_baseline"]
        self.assertEqual(rust["ownership_probe_binary"], "syntavra-remaining71-ownership")
        self.assertNotIn("module_ownership_probe_binary", rust)
        self.assertTrue(
            self.contract["policy"]["combined_selector_and_lower_module_ownership_probe"]
        )
        self.assertTrue(
            self.contract["policy"]["lower_rust_owner_module_must_be_resolved_before_section_a_closure"]
        )

    def test_family_program_catalog_is_unique_and_explicit(self) -> None:
        programs = matrix._program_index(self.contract)
        self.assertEqual(len(programs), 14)
        self.assertEqual(
            set(programs),
            {
                "agent",
                "headless",
                "graph-language-semantic",
                "memory-intelligence",
                "capability-inventory",
                "provider-proxy",
                "sandbox-security",
                "core-legacy-route-reference",
                "platform-helper-evidence",
                "benchmark-proof",
                "context-compaction",
                "setup-host",
                "mcp-integration",
                "publication-registry",
            },
        )
        self.assertEqual(
            programs["publication-registry"]["state"],
            "no-public-route-family",
        )

    def test_route_digest_matches_canonical_report_encoding(self) -> None:
        self.assertEqual(
            matrix._route_digest(["b route", "a route", "a route"]),
            matrix._route_digest(["a route", "b route"]),
        )
        self.assertEqual(len(matrix._route_digest(["a route"])), 64)

    def test_claim_boundary_does_not_overclaim_module_parity(self) -> None:
        self.assertTrue(
            self.contract["policy"]["lower_rust_owner_module_must_be_resolved_before_section_a_closure"]
        )
        self.assertTrue(
            self.contract["policy"]["selector_ownership_is_not_behavioral_parity"]
        )
        self.assertTrue(self.contract["policy"]["no_native_counter_change_in_this_gate"])


if __name__ == "__main__":
    unittest.main()
