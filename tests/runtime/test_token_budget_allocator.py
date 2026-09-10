from __future__ import annotations

from dataclasses import replace
import unittest

from syntavra_runtime.provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelopeCompiler,
    TokenEnvelopePolicy,
)
from syntavra_runtime.context_governor import (
    AuctionContext,
    AuctionCostEvent,
    LaneBudgetRequest,
    MarginalTokenBid,
    TokenBudgetAllocator,
    TokenBudgetAuctionDecision,
)


class TokenBudgetAllocatorTests(unittest.TestCase):
    def envelope(self):
        policy = TokenEnvelopePolicy(
            target_total_tokens=140,
            target_input_tokens=100,
            target_output_tokens=40,
            input_target_fraction=1.0,
            output_target_fraction=1.0,
            minimum_avoidable_reduction=0.0,
            minimum_input_budget_tokens=1,
            minimum_output_budget_tokens=1,
        )
        return ProviderTokenEnvelopeCompiler(policy).compile(
            original_input_tokens=100,
            original_output_budget_tokens=40,
            mandatory_input_tokens=30,
            mandatory_output_tokens=10,
            input_leases=(
                NecessityLease("user", "user_instruction", 20, "frozen:user"),
                NecessityLease("verifier", "verifier_evidence", 10, "frozen:verifier", exact_required=True),
            ),
            output_leases=(
                NecessityLease("requested", "requested_output", 10, "frozen:output"),
            ),
        )

    @staticmethod
    def context(mode: str = "shadow") -> AuctionContext:
        return AuctionContext(
            "openai",
            "gpt-test",
            "scoped-coding",
            "target-tokenizer-v1",
            "frozen:task-set-v1",
            "frozen:verifier-set-v1",
            mode,
        )

    @staticmethod
    def requests():
        return (
            LaneBudgetRequest(
                "retrieval", 35, 10, 60, "min:retrieval",
                (
                    MarginalTokenBid("r1", "retrieval", 20, 8.0, "trace:r1", 0),
                    MarginalTokenBid("r2", "retrieval", 30, 3.0, "trace:r2", 1),
                ),
            ),
            LaneBudgetRequest(
                "instructions_context", 40, 15, 50, "min:instructions",
                (MarginalTokenBid("i1", "instructions_context", 20, 6.0, "trace:i1", 0),),
            ),
            LaneBudgetRequest(
                "schema_tool_output", 25, 5, 30, "min:schema",
                (MarginalTokenBid("s1", "schema_tool_output", 20, 4.0, "trace:s1", 0),),
            ),
            LaneBudgetRequest(
                "reasoning", 20, 5, 30, "min:reasoning",
                (MarginalTokenBid("q1", "reasoning", 25, 10.0, "trace:q1", 0),),
            ),
            LaneBudgetRequest(
                "output", 20, 5, 30, "min:output",
                (MarginalTokenBid("o1", "output", 25, 5.0, "trace:o1", 0),),
            ),
        )

    def test_shadow_mode_recommends_but_preserves_fixed_effective_budget(self):
        decision = TokenBudgetAllocator().allocate(
            envelope=self.envelope(), context=self.context(), requests=self.requests()
        )
        self.assertEqual(decision.effective_allocation, decision.fixed_allocation)
        self.assertNotEqual(decision.recommended_allocation, decision.fixed_allocation)
        self.assertEqual(sum(decision.recommended_allocation[l] for l in ("retrieval", "instructions_context", "schema_tool_output")), 100)
        self.assertEqual(sum(decision.recommended_allocation[l] for l in ("reasoning", "output")), 40)
        self.assertGreaterEqual(
            decision.recommended_estimated_verified_gain,
            decision.fixed_estimated_verified_gain,
        )
        self.assertIn("SHADOW_RECOMMENDATION_NOT_APPLIED", decision.reason_codes)

    def test_offline_mode_applies_recommendation_only_offline(self):
        decision = TokenBudgetAllocator().allocate(
            envelope=self.envelope(), context=self.context("offline"), requests=self.requests()
        )
        self.assertEqual(decision.effective_allocation, decision.recommended_allocation)
        self.assertIn("OFFLINE_RECOMMENDATION_APPLIED", decision.reason_codes)

    def test_live_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.context("live")

    def test_hard_minima_cannot_drop_below_envelope_mandatory_tokens(self):
        rows = list(self.requests())
        rows[0] = replace(rows[0], hard_min_tokens=0, hard_min_evidence_ref="")
        with self.assertRaises(ValueError):
            TokenBudgetAllocator().allocate(
                envelope=self.envelope(), context=self.context(), requests=rows
            )

    def test_fixed_baseline_must_exactly_partition_envelope(self):
        rows = list(self.requests())
        rows[0] = replace(rows[0], fixed_tokens=34)
        with self.assertRaises(ValueError):
            TokenBudgetAllocator().allocate(
                envelope=self.envelope(), context=self.context(), requests=rows
            )

    def test_requires_exactly_five_canonical_lanes(self):
        with self.assertRaises(ValueError):
            TokenBudgetAllocator().allocate(
                envelope=self.envelope(), context=self.context(), requests=self.requests()[:-1]
            )

    def test_envelope_rejection_is_not_bypassed(self):
        envelope = replace(self.envelope(), provider_call_admissible=False)
        with self.assertRaises(ValueError):
            TokenBudgetAllocator().allocate(
                envelope=envelope, context=self.context(), requests=self.requests()
            )

    def test_per_lane_marginal_value_must_be_non_increasing(self):
        with self.assertRaises(ValueError):
            LaneBudgetRequest(
                "retrieval", 20, 10, 40, "min:r",
                (
                    MarginalTokenBid("r1", "retrieval", 10, 1.0, "e1", 0),
                    MarginalTokenBid("r2", "retrieval", 10, 5.0, "e2", 1),
                ),
            )

    def test_decision_receipt_is_deterministic_and_tamper_evident(self):
        allocator = TokenBudgetAllocator()
        left = allocator.allocate(
            envelope=self.envelope(), context=self.context(), requests=self.requests()
        )
        right = TokenBudgetAllocator().allocate(
            envelope=self.envelope(), context=self.context(), requests=self.requests()
        )
        self.assertEqual(left.receipt_hash, right.receipt_hash)
        self.assertTrue(TokenBudgetAuctionDecision.verify_receipt(left.receipt()))
        tampered = left.receipt()
        tampered["recommended_allocation"]["retrieval"] += 1
        with self.assertRaises(ValueError):
            TokenBudgetAuctionDecision.verify_receipt(tampered)

    def test_retry_recall_repair_and_fallback_count_end_to_end(self):
        allocator = TokenBudgetAllocator()
        decision = allocator.allocate(
            envelope=self.envelope(), context=self.context(), requests=self.requests()
        )
        for index, kind in enumerate(("retry", "recall", "repair", "fallback"), start=1):
            allocator.record_overhead(
                AuctionCostEvent(
                    f"event-{index}", kind, "retrieval", index,
                    f"trace:{kind}", "a" * 64, "b" * 64,
                )
            )
        receipt = allocator.accounting_receipt(
            decision,
            baseline_usage_receipt_hash="c" * 64,
            baseline_token_receipt_hash="d" * 64,
            optimized_usage_receipt_hash="e" * 64,
            optimized_token_receipt_hash="f" * 64,
        )
        self.assertEqual(receipt["overhead_provider_visible_tokens"], 10)
        self.assertEqual(receipt["provider_verified_overhead_tokens"], 10)
        self.assertTrue(receipt["paired_execution_evidence_complete"])
        self.assertFalse(receipt["provider_savings_claim"])
        self.assertTrue(TokenBudgetAllocator.verify_accounting_receipt(receipt))

    def test_unpaired_provider_receipts_are_rejected(self):
        with self.assertRaises(ValueError):
            AuctionCostEvent("e1", "retry", "output", 3, "trace:e1", "a" * 64, "")
        decision = TokenBudgetAllocator().allocate(
            envelope=self.envelope(), context=self.context(), requests=self.requests()
        )
        with self.assertRaises(ValueError):
            TokenBudgetAllocator().accounting_receipt(
                decision, baseline_usage_receipt_hash="a" * 64
            )

    def test_duplicate_overhead_identity_is_rejected(self):
        allocator = TokenBudgetAllocator()
        event = AuctionCostEvent("e1", "retry", "output", 3, "trace:e1")
        allocator.record_overhead(event)
        with self.assertRaises(ValueError):
            allocator.record_overhead(event)

    def test_status_keeps_existing_authorities_and_claim_boundary(self):
        status = TokenBudgetAllocator.status()
        self.assertTrue(status["provider_envelope_remains_budget_authority"])
        self.assertFalse(status["allocator_owns_context_selection"])
        self.assertFalse(status["allocator_owns_reasoning_policy"])
        self.assertFalse(status["allocator_owns_output_rendering"])
        self.assertTrue(status["shadow_or_offline_only"])
        self.assertFalse(status["provider_savings_claim"])


if __name__ == "__main__":
    unittest.main()
