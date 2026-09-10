from __future__ import annotations

import unittest

from syntavra_runtime.context_value_predictor import (
    ContextValuePredictor,
    FutureReuseObservation,
    FutureReuseRetentionPolicy,
    RetentionCandidate,
    RetentionEvaluation,
)
from syntavra_runtime.context_governor import ContextCostEstimate, ReacquisitionTaxGovernor


A = "a" * 64


class FutureReuseRetentionPredictorTests(unittest.TestCase):
    def _accounting(self) -> dict:
        governor = ReacquisitionTaxGovernor()
        used = governor.decide(
            identity="used",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="used",
                expected_carry_tokens=100,
                expected_retrieval_tokens=20,
                expected_tool_tokens=10,
            ),
            policy_hash=A,
        )
        unused = governor.decide(
            identity="unused",
            original_action="EXTERNALIZE",
            estimate=ContextCostEstimate(
                identity="unused",
                expected_carry_tokens=100,
                expected_retrieval_tokens=10,
            ),
            policy_hash=A,
        )
        self.assertEqual(used.outcome, "PRUNE")
        self.assertEqual(unused.outcome, "PRUNE")
        governor.record_reacquisition(
            identity="used",
            event_kind="tool",
            retrieval_tokens=30,
            tool_tokens=30,
            source_ref="frozen:repo.read",
        )
        return governor.accounting_receipt()

    def _fitted(self) -> ContextValuePredictor:
        predictor = ContextValuePredictor()
        observations = predictor.observations_from_accounting(
            self._accounting(),
            horizon_complete=True,
            feature_by_identity={"used": "edit", "unused": "edit"},
        )
        predictor.fit(observations)
        return predictor

    def test_complete_horizon_derives_positive_and_negative_labels(self) -> None:
        observations = ContextValuePredictor.observations_from_accounting(
            self._accounting(),
            horizon_complete=True,
            feature_by_identity={"used": "edit", "unused": "edit"},
        )
        self.assertEqual([item.future_used for item in observations], [True, False])
        by_id = {item.identity: item for item in observations}
        self.assertEqual(by_id["used"].observed_reacquisition_cost_units, 60)
        self.assertEqual(by_id["unused"].observed_reacquisition_cost_units, 0)

    def test_incomplete_horizon_is_unlabeled_and_excluded(self) -> None:
        predictor = ContextValuePredictor()
        observations = predictor.observations_from_accounting(
            self._accounting(),
            horizon_complete=False,
        )
        self.assertTrue(all(item.future_used is None for item in observations))
        receipt = predictor.fit(observations)
        self.assertEqual(receipt["complete_label_count"], 0)
        self.assertEqual(receipt["excluded_incomplete_count"], 2)
        estimate = predictor.estimate("global", fallback_reacquisition_cost_units=80)
        self.assertEqual(estimate.future_use_probability, 0.5)
        self.assertEqual(estimate.expected_reacquisition_cost_units, 80)

    def test_beta_smoothed_probability_and_positive_cost_are_deterministic(self) -> None:
        predictor = self._fitted()
        one = predictor.estimate("edit", fallback_reacquisition_cost_units=999)
        two = predictor.estimate("edit", fallback_reacquisition_cost_units=999)
        self.assertEqual(one.estimate_hash, two.estimate_hash)
        self.assertEqual(one.future_use_probability, 0.5)
        self.assertEqual(one.expected_reacquisition_cost_units, 60)
        self.assertEqual((one.positive_labels, one.negative_labels), (1, 1))

    def test_positive_retention_value_is_shadow_recommendation_only(self) -> None:
        predictor = self._fitted()
        result = predictor.predict(
            RetentionCandidate(
                identity="candidate",
                baseline_action="EXTERNALIZE",
                feature_key="edit",
                carry_cost_units=10,
                fallback_reacquisition_cost_units=60,
            )
        )
        self.assertEqual(result.recommended_action, "KEEP")
        self.assertEqual(result.effective_action, "EXTERNALIZE")
        self.assertGreater(result.retention_value, 0)
        self.assertTrue(result.shadow_mode)

    def test_non_positive_retention_value_preserves_baseline(self) -> None:
        predictor = self._fitted()
        result = predictor.predict(
            RetentionCandidate(
                identity="candidate",
                baseline_action="COMPRESS",
                feature_key="edit",
                carry_cost_units=100,
                fallback_reacquisition_cost_units=60,
            )
        )
        self.assertEqual(result.recommended_action, "COMPRESS")
        self.assertEqual(result.effective_action, "COMPRESS")
        self.assertLessEqual(result.retention_value, 0)

    def test_mandatory_failure_security_verifier_and_exact_evidence_are_hard_pinned(self) -> None:
        flags = (
            {"mandatory": True},
            {"exact_required": True},
            {"mandatory_failure_evidence": True},
            {"mandatory_security_evidence": True},
            {"mandatory_verifier_evidence": True},
        )
        predictor = self._fitted()
        for index, kwargs in enumerate(flags):
            with self.subTest(index=index):
                result = predictor.predict(
                    RetentionCandidate(
                        identity=f"pinned-{index}",
                        baseline_action="EXTERNALIZE",
                        feature_key="edit",
                        carry_cost_units=999,
                        fallback_reacquisition_cost_units=1,
                        **kwargs,
                    )
                )
                self.assertTrue(result.hard_pinned)
                self.assertEqual(result.recommended_action, "KEEP")
                self.assertEqual(result.effective_action, "KEEP")

    def test_invalidation_is_not_overridden_by_learned_retention(self) -> None:
        predictor = self._fitted()
        result = predictor.predict(
            RetentionCandidate(
                identity="stale",
                baseline_action="ABSTAIN",
                feature_key="edit",
                carry_cost_units=1,
                fallback_reacquisition_cost_units=1000,
                invalidated=True,
            )
        )
        self.assertEqual(result.recommended_action, "ABSTAIN")
        self.assertEqual(result.effective_action, "ABSTAIN")
        self.assertIn("INVALIDATION_OUTSIDE_LEARNED_RETENTION", result.reason_codes)

    def test_default_policy_never_promotes_even_when_evaluation_is_clean(self) -> None:
        predictor = self._fitted()
        gate = predictor.evaluate_promotion(
            RetentionEvaluation(
                baseline_task_success_rate=1.0,
                candidate_task_success_rate=1.0,
                mandatory_evidence_misses=0,
                baseline_reacquisition_cost_units=50,
                candidate_reacquisition_cost_units=40,
                baseline_provider_cost_per_success=100,
                candidate_provider_cost_per_success=90,
            )
        )
        self.assertTrue(gate["evidence_gate_ok"])
        self.assertFalse(gate["promotion_allowed"])
        self.assertFalse(gate["provider_savings_claim"])
        self.assertTrue(ContextValuePredictor.verify_receipt(gate))

    def test_promotion_gate_rolls_back_on_quality_or_cost_regression(self) -> None:
        predictor = ContextValuePredictor(
            FutureReuseRetentionPolicy(shadow_mode=False, promotion_enabled=True)
        )
        gate = predictor.evaluate_promotion(
            RetentionEvaluation(
                baseline_task_success_rate=1.0,
                candidate_task_success_rate=0.9,
                mandatory_evidence_misses=1,
                baseline_reacquisition_cost_units=20,
                candidate_reacquisition_cost_units=30,
                baseline_provider_cost_per_success=100,
                candidate_provider_cost_per_success=110,
            )
        )
        self.assertFalse(gate["promotion_allowed"])
        self.assertEqual(
            set(gate["reason_codes"]),
            {
                "MANDATORY_EVIDENCE_MISS",
                "TASK_SUCCESS_REGRESSION",
                "REACQUISITION_COST_REGRESSION",
                "PROVIDER_COST_PER_SUCCESS_REGRESSION",
            },
        )

    def test_explicit_non_shadow_promotion_can_apply_only_after_clean_gate(self) -> None:
        predictor = ContextValuePredictor(
            FutureReuseRetentionPolicy(shadow_mode=False, promotion_enabled=True)
        )
        observations = predictor.observations_from_accounting(
            self._accounting(),
            horizon_complete=True,
            feature_by_identity={"used": "edit", "unused": "edit"},
        )
        predictor.fit(observations)
        gate = predictor.evaluate_promotion(
            RetentionEvaluation(1.0, 1.0, 0, 60, 50, 100, 95)
        )
        self.assertTrue(gate["promotion_allowed"])
        result = predictor.predict(
            RetentionCandidate(
                identity="candidate",
                baseline_action="EXTERNALIZE",
                feature_key="edit",
                carry_cost_units=10,
                fallback_reacquisition_cost_units=60,
            )
        )
        self.assertEqual(result.recommended_action, "KEEP")
        self.assertEqual(result.effective_action, "KEEP")

    def test_fit_and_status_report_zero_provider_work(self) -> None:
        predictor = ContextValuePredictor()
        fit = predictor.fit(())
        self.assertEqual(fit["provider_calls"], 0)
        self.assertEqual(fit["provider_tokens"], 0)
        self.assertFalse(fit["provider_savings_claim"])
        status = predictor.status()
        self.assertEqual(status["provider_calls"], 0)
        self.assertEqual(status["provider_tokens"], 0)
        self.assertFalse(status["raw_context_authority"])
        self.assertFalse(status["semantic_state_authority"])

    def test_receipt_tamper_is_rejected(self) -> None:
        predictor = ContextValuePredictor()
        receipt = predictor.fit(())
        receipt["complete_label_count"] = 7
        with self.assertRaises(ValueError):
            ContextValuePredictor.verify_receipt(receipt)

    def test_observation_horizon_invariants_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            FutureReuseObservation("x", "global", True, None, 0, 0, "", "")
        with self.assertRaises(ValueError):
            FutureReuseObservation("x", "global", False, False, 0, 0, "", "")


if __name__ == "__main__":
    unittest.main()
