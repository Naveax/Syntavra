from __future__ import annotations

import unittest

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
)
from syntavra_runtime.context_decision_trace import ContextDecisionTrace
from syntavra_runtime.context_governor import (
    ContextCostEstimate,
    ReacquisitionTaxGovernor,
    ReacquisitionTaxPolicy,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64


class ReacquisitionTaxGovernorTests(unittest.TestCase):
    def _policy_result(self) -> dict:
        policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=512))
        return policy.evaluate(
            "edit one file",
            [
                ContextPolicySignal(
                    identity="ctx-1",
                    token_count=200,
                    relevance=0.10,
                    recoverable=True,
                    exact_required=False,
                )
            ],
        )

    def test_refuses_prune_when_reacquisition_exceeds_carry(self) -> None:
        governor = ReacquisitionTaxGovernor()
        result = governor.govern_policy_result(
            self._policy_result(),
            [
                ContextCostEstimate(
                    identity="ctx-1",
                    expected_carry_tokens=40,
                    expected_retrieval_tokens=65,
                    expected_tool_tokens=20,
                    tokenizer_method="LOCALLY_TOKENIZED:test",
                    exact_target_tokenizer=True,
                )
            ],
        )
        decision = result["decisions"][0]
        self.assertEqual(decision["recommended_action"], "KEEP")
        self.assertIn("REACQUISITION_COST_EXCEEDS_CARRY", decision["reason_codes"])
        self.assertTrue(ReacquisitionTaxGovernor.verify_receipt(result["reacquisition_tax_receipt"]))
        self.assertTrue(ContextDecisionTrace.from_policy_result(result)["event_count"] >= 2)

    def test_allows_prune_when_reacquisition_is_not_dearer(self) -> None:
        governor = ReacquisitionTaxGovernor()
        original = self._policy_result()
        original_action = original["decisions"][0]["recommended_action"]
        self.assertIn(original_action, {"SUMMARIZE", "COMPRESS", "EXTERNALIZE"})
        result = governor.govern_policy_result(
            original,
            [
                ContextCostEstimate(
                    identity="ctx-1",
                    expected_carry_tokens=120,
                    expected_retrieval_tokens=10,
                    expected_tool_tokens=5,
                    tokenizer_method="LOCALLY_TOKENIZED:test",
                    exact_target_tokenizer=True,
                )
            ],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], original_action)
        self.assertEqual(result["reacquisition_tax_receipt"]["pruned_count"], 1)

    def test_missing_estimate_fails_closed(self) -> None:
        governor = ReacquisitionTaxGovernor()
        result = governor.govern_policy_result(self._policy_result(), [])
        self.assertEqual(result["decisions"][0]["recommended_action"], "KEEP")
        self.assertIn(
            "REACQUISITION_ESTIMATE_MISSING_FAIL_CLOSED",
            result["decisions"][0]["reason_codes"],
        )

    def test_invalidation_and_security_can_force_drop(self) -> None:
        governor = ReacquisitionTaxGovernor()
        invalidated = governor.decide(
            identity="stale",
            original_action="KEEP",
            estimate=ContextCostEstimate(
                identity="stale",
                expected_carry_tokens=1,
                expected_provider_tokens=1000,
                invalidated=True,
            ),
            policy_hash=A,
        )
        self.assertEqual(invalidated.outcome, "FORCED_DROP")
        self.assertEqual(invalidated.governed_action, "ABSTAIN")

        security = governor.decide(
            identity="secret",
            original_action="KEEP",
            estimate=ContextCostEstimate(
                identity="secret",
                expected_carry_tokens=1,
                expected_provider_tokens=1000,
                security_forced_drop=True,
            ),
            policy_hash=A,
        )
        self.assertEqual(security.outcome, "FORCED_DROP")
        self.assertEqual(security.governed_action, "ABSTAIN")

    def test_mandatory_context_is_pinned(self) -> None:
        governor = ReacquisitionTaxGovernor()
        decision = governor.decide(
            identity="mandatory",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="mandatory",
                expected_carry_tokens=500,
                expected_retrieval_tokens=1,
                mandatory=True,
            ),
            policy_hash=A,
        )
        self.assertEqual(decision.outcome, "RETAIN")
        self.assertEqual(decision.governed_action, "KEEP")

    def test_rollback_disables_economic_pruning_safely(self) -> None:
        governor = ReacquisitionTaxGovernor(ReacquisitionTaxPolicy(enabled=False))
        decision = governor.decide(
            identity="rollback",
            original_action="COMPRESS",
            estimate=ContextCostEstimate(
                identity="rollback",
                expected_carry_tokens=1000,
                expected_retrieval_tokens=1,
            ),
            policy_hash=A,
        )
        self.assertEqual(decision.governed_action, "KEEP")
        self.assertIn("ROLLBACK_SAFE_RETAIN", decision.reason_codes[0])

    def test_later_tool_reacquisition_is_attributed_to_prior_prune(self) -> None:
        governor = ReacquisitionTaxGovernor()
        drop = governor.decide(
            identity="ctx",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="ctx",
                expected_carry_tokens=100,
                expected_retrieval_tokens=10,
            ),
            policy_hash=A,
        )
        self.assertEqual(drop.outcome, "PRUNE")
        event = governor.record_reacquisition(
            identity="ctx",
            event_kind="tool",
            retrieval_tokens=10,
            tool_tokens=20,
            latency_ms=4.5,
            source_ref="tool:repo.read",
        )
        self.assertTrue(event.attributed)
        self.assertEqual(event.drop_decision_hash, drop.decision_hash)
        accounting = governor.accounting_receipt()
        self.assertEqual(accounting["counterfactual_retention_tokens"], 100)
        self.assertEqual(accounting["reacquisition_tokens"]["total"], 30)
        self.assertEqual(accounting["accounted_net_token_delta"], -70)
        self.assertTrue(ReacquisitionTaxGovernor.verify_receipt(accounting))

    def test_unattributed_read_is_not_charged_to_pruning(self) -> None:
        governor = ReacquisitionTaxGovernor()
        event = governor.record_reacquisition(
            identity="never-dropped",
            event_kind="read",
            retrieval_tokens=12,
        )
        self.assertFalse(event.attributed)
        accounting = governor.accounting_receipt()
        self.assertEqual(accounting["reacquisition_tokens"]["total"], 0)
        self.assertEqual(accounting["unattributed_event_count"], 1)

    def test_provider_work_without_receipts_cannot_be_provider_verified(self) -> None:
        governor = ReacquisitionTaxGovernor()
        governor.decide(
            identity="ctx",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="ctx",
                expected_carry_tokens=100,
                expected_provider_tokens=10,
            ),
            policy_hash=A,
        )
        governor.record_reacquisition(identity="ctx", event_kind="provider", provider_tokens=5)
        accounting = governor.accounting_receipt()
        self.assertFalse(accounting["reacquisition_provider_evidence_complete"])
        self.assertFalse(accounting["provider_verified"])
        self.assertIsNone(accounting["provider_verified_net_token_delta"])
        self.assertFalse(accounting["provider_savings_claim"])

    def test_paired_provider_receipts_enable_verified_accounting_not_savings_claim(self) -> None:
        governor = ReacquisitionTaxGovernor()
        governor.decide(
            identity="ctx",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="ctx",
                expected_carry_tokens=100,
                expected_provider_tokens=10,
                baseline_usage_receipt_hash=A,
                baseline_token_receipt_hash=B,
            ),
            policy_hash=C,
        )
        governor.record_reacquisition(
            identity="ctx",
            event_kind="provider",
            provider_tokens=5,
            usage_receipt_hash=C,
            token_receipt_hash=D,
        )
        accounting = governor.accounting_receipt()
        self.assertTrue(accounting["baseline_provider_evidence_complete"])
        self.assertTrue(accounting["reacquisition_provider_evidence_complete"])
        self.assertTrue(accounting["provider_verified"])
        self.assertEqual(accounting["provider_verified_net_token_delta"], -95)
        self.assertFalse(accounting["provider_savings_claim"])

    def test_decision_receipt_is_deterministic(self) -> None:
        estimate = ContextCostEstimate(
            identity="ctx",
            expected_carry_tokens=100,
            expected_retrieval_tokens=20,
            tokenizer_method="LOCAL:test",
        )
        one = ReacquisitionTaxGovernor().decide(
            identity="ctx", original_action="EXTERNALIZE", estimate=estimate, policy_hash=A
        )
        two = ReacquisitionTaxGovernor().decide(
            identity="ctx", original_action="EXTERNALIZE", estimate=estimate, policy_hash=A
        )
        self.assertEqual(one.decision_hash, two.decision_hash)

    def test_weighted_latency_can_flip_retention_decision(self) -> None:
        governor = ReacquisitionTaxGovernor(
            ReacquisitionTaxPolicy(latency_token_equivalent_per_ms=1.0)
        )
        decision = governor.decide(
            identity="ctx",
            original_action="COMPRESS",
            estimate=ContextCostEstimate(
                identity="ctx",
                expected_carry_tokens=100,
                expected_retrieval_tokens=20,
                expected_latency_ms=90,
            ),
            policy_hash=A,
        )
        self.assertEqual(decision.outcome, "RETAIN")

    def test_negative_costs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ContextCostEstimate(identity="bad", expected_carry_tokens=-1)
        governor = ReacquisitionTaxGovernor()
        with self.assertRaises(ValueError):
            governor.record_reacquisition(identity="bad", event_kind="tool", tool_tokens=-1)


if __name__ == "__main__":
    unittest.main()
