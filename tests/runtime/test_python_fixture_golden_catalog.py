from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_fixture_golden_catalog import certify


class PythonFixtureGoldenCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_catalog_identity_and_family_scope_are_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["family"], "fixture-golden-catalog")
        self.assertEqual(self.report["family_count"], 13)
        self.assertEqual(self.report["canonical_public_route_count"], 245)
        self.assertEqual(len(self.report["canonical_public_route_sha256"]), 64)

    def test_fixture_coverage_is_complete_or_explicitly_not_applicable(self) -> None:
        self.assertEqual(self.report["fixture_case_count"], 65)
        self.assertEqual(
            self.report["coverage"],
            {"covered": 59, "not_applicable": 6, "missing": 0},
        )
        self.assertEqual(self.report["deterministic_snapshot_count"], 13)

    def test_rust_can_consume_catalog_without_python_runtime(self) -> None:
        self.assertFalse(self.report["rust_python_required"])
        self.assertEqual(self.report["static_contract_count"], 9)
        self.assertEqual(self.report["inline_static_family_count"], 4)
        self.assertTrue(
            all(not row["python_required_by_rust"] for row in self.report["family_summaries"])
        )
        self.assertTrue(
            all(row["catalog_pointer"].startswith("/families/") for row in self.report["family_summaries"])
        )

    def test_every_family_has_a_deterministic_snapshot_and_explicit_nondeterminism(self) -> None:
        self.assertEqual(len(self.report["family_summaries"]), 13)
        for row in self.report["family_summaries"]:
            self.assertEqual(len(row["snapshot_sha256"]), 64)
            self.assertGreaterEqual(row["covered"], 4)
            self.assertGreaterEqual(row["nondeterministic_field_count"], 0)

    def test_catalog_itself_is_machine_readable_and_content_addressed(self) -> None:
        self.assertEqual(len(self.report["catalog_file_sha256"]), 64)
        self.assertEqual(len(self.report["catalog_semantic_sha256"]), 64)
        self.assertEqual(
            self.report["nondeterministic_policy"],
            "only fields explicitly listed per family may be normalized by later Rust differential tests",
        )


if __name__ == "__main__":
    unittest.main()
