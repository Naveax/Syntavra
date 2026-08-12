from __future__ import annotations

import unittest
from pathlib import Path

from tools.certify_python_benchmark_proof_reference import certify


class PythonBenchmarkProofReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = certify(Path(__file__).resolve().parents[2])

    def test_public_benchmark_proof_inventory_is_frozen(self) -> None:
        self.assertTrue(self.report["ok"], self.report)
        self.assertEqual(self.report["routes"]["route_count"], 20)
        self.assertEqual(len(self.report["routes"]["ownership"]), 20)
        self.assertEqual(len(self.report["routes"]["route_sha256"]), 64)

    def test_thresholds_and_pair_calculations_are_frozen(self) -> None:
        thresholds = self.report["thresholds"]
        self.assertEqual(thresholds["signalbench"]["required_tasks"], 150)
        self.assertEqual(thresholds["signalbench"]["required_repetitions"], 30)
        self.assertEqual(thresholds["measured_benchmark"]["minimum_pairs"], 30)
        self.assertEqual(thresholds["external_suite"]["minimum_pairs"], 30)
        self.assertEqual(thresholds["live_integration"]["minimum_receipts_per_integration"], 3)
        self.assertEqual(self.report["benchmark"]["comparison"]["valid_pairs"], 10)
        self.assertEqual(self.report["benchmark"]["comparison"]["diagnostics"]["token_ratios"], [10.0] * 10)

    def test_signalbench_plan_gate_and_empty_state_are_deterministic(self) -> None:
        signalbench = self.report["signalbench"]
        self.assertEqual(signalbench["plan"]["tasks"], 150)
        self.assertEqual(signalbench["plan"]["required_pairs"], 4500)
        self.assertEqual(signalbench["fixture_rows"], 9000)
        self.assertTrue(signalbench["gate"]["ok"])
        self.assertEqual(signalbench["gate"]["metrics"]["pairs"], 4500)
        self.assertEqual(signalbench["gate"]["metrics"]["token_ratio"], 0.1)
        self.assertEqual(signalbench["gate"]["metrics"]["wall_ratio"], 0.1)
        self.assertFalse(signalbench["empty"]["ok"])
        self.assertIn("empty-results", signalbench["empty"]["reasons"])

    def test_proof_gate_statistics_and_ordering_are_frozen(self) -> None:
        proof = self.report["proof"]
        self.assertEqual(proof["receipts"]["total"], 60)
        self.assertTrue(proof["measured_benchmark"]["ok"])
        self.assertEqual(proof["measured_benchmark"]["metrics"]["pairs"], 30)
        self.assertEqual(proof["measured_benchmark"]["metrics"]["mean_token_ratio"], 0.1)
        self.assertTrue(proof["provider_billed"]["claimable_superiority"])
        self.assertEqual(proof["provider_billed"]["confidence_interval_95"], [2.0, 2.0])
        self.assertTrue(proof["external_suite"]["ok"])
        self.assertEqual(proof["external_suite"]["metrics"]["pairs"], 30)
        self.assertTrue(proof["integrations"]["ok"])
        self.assertEqual(proof["suites"]["suite_count"], 5)
        self.assertEqual(proof["maturity_empty"]["exit"], 4)
        self.assertEqual(proof["readiness_empty"]["exit"], 4)

    def test_empty_state_and_malformed_sample_behavior_are_explicit(self) -> None:
        empty = self.report["empty_state"]
        self.assertFalse(empty["measured"]["ok"])
        self.assertFalse(empty["external"]["ok"])
        self.assertFalse(empty["integration"]["ok"])
        self.assertEqual(self.report["proof"]["malformed_provider_raw"]["exit"], 4)
        self.assertFalse(self.report["proof"]["malformed_provider_raw"]["stderr_nonempty"])
        self.assertTrue(self.report["proof"]["malformed_provider_raw"]["json_object"])
        self.assertEqual(self.report["proof"]["malformed_external_raw"]["exit"], 4)
        self.assertFalse(self.report["proof"]["malformed_external_raw"]["stderr_nonempty"])
        self.assertTrue(self.report["proof"]["malformed_external_raw"]["json_object"])

    def test_claim_and_network_boundaries_are_not_overstated(self) -> None:
        self.assertEqual(
            self.report["claim_boundary"],
            "offline deterministic certification fixtures validate Python gate semantics only; they are not real external superiority evidence",
        )
        self.assertEqual(
            self.report["network_boundary"],
            "offline deterministic fixtures only; no live provider, repository benchmark, or SaaS execution",
        )


if __name__ == "__main__":
    unittest.main()
