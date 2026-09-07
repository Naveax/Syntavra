from __future__ import annotations

import unittest

from syntavra_runtime.provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelopeCompiler,
    TokenEnvelopePolicy,
)


class ProviderTokenEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = ProviderTokenEnvelopeCompiler()

    def test_large_ordinary_workload_targets_eight_thousand_total(self) -> None:
        input_leases = (
            NecessityLease("system", "system_instruction", 800, "request:system", True),
            NecessityLease("user", "user_instruction", 1_200, "request:user", True),
        )
        output_leases = (
            NecessityLease("answer", "requested_output", 400, "request:answer", True),
        )
        envelope = self.compiler.compile_from_leases(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            input_leases=input_leases,
            output_leases=output_leases,
        )
        self.assertEqual(envelope.provider_input_budget_tokens, 6_000)
        self.assertEqual(envelope.provider_output_budget_tokens, 2_000)
        self.assertEqual(envelope.provider_total_budget_tokens, 8_000)
        self.assertTrue(envelope.target_zone_satisfied)
        self.assertTrue(envelope.provider_call_admissible)
        self.assertGreaterEqual(envelope.input_avoidable_reduction_ratio, 0.80)
        self.assertGreaterEqual(envelope.output_avoidable_reduction_ratio, 0.80)
        self.assertGreater(envelope.total_reduction_ratio, 0.90)

    def test_irreducible_exact_input_can_overflow_only_with_proof(self) -> None:
        envelope = self.compiler.compile_from_leases(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            input_leases=(
                NecessityLease(
                    "exact-source",
                    "exact_source",
                    25_000,
                    "artifact:sha256:abc",
                    True,
                ),
            ),
        )
        self.assertEqual(envelope.provider_input_budget_tokens, 25_000)
        self.assertEqual(envelope.overflow_tokens, 19_000)
        self.assertFalse(envelope.target_zone_satisfied)
        self.assertTrue(envelope.necessity_proof_complete)
        self.assertTrue(envelope.provider_call_admissible)
        self.assertIn(
            "IRREDUCIBLE_OVERFLOW_REQUIRES_NECESSITY_PROOF",
            envelope.reason_codes,
        )

    def test_unproved_mandatory_tokens_fail_closed(self) -> None:
        envelope = self.compiler.compile(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            mandatory_input_tokens=2_000,
            mandatory_output_tokens=400,
        )
        self.assertFalse(envelope.necessity_proof_complete)
        self.assertFalse(envelope.provider_call_admissible)
        self.assertIn("MANDATORY_TOKENS_LACK_NECESSITY_PROOF", envelope.reason_codes)

    def test_explicit_large_output_is_not_silently_truncated(self) -> None:
        envelope = self.compiler.compile_from_leases(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            input_leases=(
                NecessityLease("user", "user_instruction", 1_000, "request:user", True),
            ),
            output_leases=(
                NecessityLease(
                    "verbatim-output",
                    "requested_output",
                    5_000,
                    "request:verbatim-output",
                    True,
                ),
            ),
        )
        self.assertEqual(envelope.provider_output_budget_tokens, 5_000)
        self.assertFalse(envelope.target_zone_satisfied)
        self.assertTrue(envelope.provider_call_admissible)
        self.assertIn("MANDATORY_OUTPUT_EXCEEDS_TARGET", envelope.reason_codes)

    def test_small_workload_never_expands_to_fill_target(self) -> None:
        envelope = self.compiler.compile_from_leases(
            original_input_tokens=500,
            original_output_budget_tokens=200,
            input_leases=(),
            output_leases=(),
        )
        self.assertLessEqual(envelope.provider_input_budget_tokens, 500)
        self.assertLessEqual(envelope.provider_output_budget_tokens, 200)
        self.assertLessEqual(envelope.provider_total_budget_tokens, 700)

    def test_duplicate_leases_are_rejected(self) -> None:
        lease = NecessityLease("same", "user_instruction", 100, "request:user", True)
        with self.assertRaises(ValueError):
            self.compiler.compile_from_leases(
                original_input_tokens=1_000,
                original_output_budget_tokens=500,
                input_leases=(lease, lease),
            )

    def test_policy_rejects_targets_that_exceed_total(self) -> None:
        with self.assertRaises(ValueError):
            TokenEnvelopePolicy(
                target_total_tokens=7_000,
                target_input_tokens=6_000,
                target_output_tokens=2_000,
            )


if __name__ == "__main__":
    unittest.main()
