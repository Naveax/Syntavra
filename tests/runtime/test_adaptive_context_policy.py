from __future__ import annotations

import unittest

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
    ContextPolicyState,
)
from syntavra_runtime.context_pack import ContextPackItem, TaskContextPack
from syntavra_runtime.optimization_modes import MODES


class AdaptiveContextPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AdaptiveContextPolicy(
            AdaptivePolicyConfig(context_budget_tokens=1000)
        )

    @staticmethod
    def signal(identity: str, **overrides: object) -> ContextPolicySignal:
        values: dict[str, object] = {
            "identity": identity,
            "token_count": 120,
            "relevance": 0.8,
            "trust": 0.95,
            "freshness": 1.0,
            "recoverable": True,
        }
        values.update(overrides)
        return ContextPolicySignal(**values)  # type: ignore[arg-type]

    def test_high_utility_context_is_kept(self) -> None:
        result = self.policy.evaluate("repair graph", [self.signal("a", relevance=1.0)])
        decision = result["decisions"][0]
        self.assertEqual(decision["recommended_action"], "KEEP")
        self.assertEqual(decision["effective_action"], "KEEP")
        self.assertEqual(result["recommended_session_action"], "KEEP")

    def test_medium_utility_context_is_summarized(self) -> None:
        result = self.policy.evaluate(
            "repair graph",
            [self.signal("a", relevance=0.55, trust=0.8, freshness=0.8)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "SUMMARIZE")

    def test_lower_utility_context_is_compressed(self) -> None:
        result = self.policy.evaluate(
            "repair graph",
            [self.signal("a", relevance=0.25, trust=0.65, freshness=0.7)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "COMPRESS")

    def test_low_utility_recoverable_context_is_externalized(self) -> None:
        result = self.policy.evaluate(
            "repair graph",
            [self.signal("a", relevance=0.0, trust=0.2, freshness=0.2)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "EXTERNALIZE")

    def test_exact_required_context_is_kept(self) -> None:
        result = self.policy.evaluate(
            "edit exact function",
            [self.signal("a", relevance=0.1, exact_required=True)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "KEEP")
        self.assertIn("EXACT_REQUIRED", result["decisions"][0]["reason_codes"])

    def test_impossible_exact_budget_abstains(self) -> None:
        policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=256))
        result = policy.evaluate(
            "edit exact function",
            [self.signal("a", token_count=300, exact_required=True)],
        )
        self.assertFalse(result["metrics"]["budget_fit"])
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")
        self.assertIn("BUDGET_CANNOT_BE_SAFELY_SATISFIED", result["session_reason_codes"])

    def test_security_deny_is_fail_closed(self) -> None:
        result = self.policy.evaluate(
            "use credential evidence",
            [self.signal("secret", security_denied=True, relevance=1.0)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "ABSTAIN")
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")
        self.assertIn("SECURITY_DENY_PRESENT", result["session_reason_codes"])

    def test_tainted_exact_required_abstains(self) -> None:
        result = self.policy.evaluate(
            "use exact tainted evidence",
            [self.signal("tainted", exact_required=True, tainted=True)],
        )
        self.assertEqual(result["decisions"][0]["recommended_action"], "ABSTAIN")
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")

    def test_irreversible_action_with_unresolved_risk_abstains(self) -> None:
        state = ContextPolicyState(
            unresolved_critical_evidence=1,
            irreversible_action_pending=True,
        )
        result = self.policy.evaluate("publish release", [self.signal("a")], state=state)
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")
        self.assertIn(
            "IRREVERSIBLE_ACTION_WITH_UNRESOLVED_RISK",
            result["session_reason_codes"],
        )

    def test_task_drift_branches(self) -> None:
        state = ContextPolicyState(task_drift=0.9, branch_allowed=True)
        result = self.policy.evaluate("new task", [self.signal("a")], state=state)
        self.assertEqual(result["recommended_session_action"], "BRANCH")
        self.assertIn("TASK_DRIFT", result["session_reason_codes"])

    def test_task_drift_without_branch_permission_abstains(self) -> None:
        state = ContextPolicyState(task_drift=0.9, branch_allowed=False)
        result = self.policy.evaluate("new task", [self.signal("a")], state=state)
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")
        self.assertIn("TASK_DRIFT_BRANCH_FORBIDDEN", result["session_reason_codes"])

    def test_recoverable_context_pressure_resets(self) -> None:
        state = ContextPolicyState(current_context_tokens=970, reset_allowed=True)
        result = self.policy.evaluate(
            "continue task",
            [self.signal("a", token_count=10, relevance=1.0, recoverable=True)],
            state=state,
        )
        self.assertEqual(result["recommended_session_action"], "RESET")
        self.assertIn("CONTEXT_PRESSURE_RECOVERABLE", result["session_reason_codes"])

    def test_reset_that_would_lose_unrecoverable_context_abstains(self) -> None:
        state = ContextPolicyState(current_context_tokens=970, reset_allowed=True)
        result = self.policy.evaluate(
            "continue task",
            [self.signal("a", token_count=10, relevance=1.0, recoverable=False)],
            state=state,
        )
        self.assertEqual(result["recommended_session_action"], "ABSTAIN")
        self.assertIn(
            "RESET_WOULD_LOSE_UNRECOVERABLE_CONTEXT",
            result["session_reason_codes"],
        )

    def test_shadow_mode_never_claims_enforcement(self) -> None:
        state = ContextPolicyState(
            current_context_tokens=970,
            reset_allowed=True,
            shadow_mode=True,
        )
        result = self.policy.evaluate(
            "continue task",
            [self.signal("a", token_count=10, relevance=1.0)],
            state=state,
        )
        self.assertEqual(result["recommended_session_action"], "RESET")
        self.assertEqual(result["effective_session_action"], "KEEP")
        self.assertEqual(result["decisions"][0]["effective_action"], "KEEP")
        self.assertTrue(result["metrics"]["shadow_mode"])
        self.assertEqual(result["metrics"]["effective_visible_tokens"], 10)

    def test_budget_pressure_economizes_low_utility_context_first(self) -> None:
        policy = AdaptiveContextPolicy(
            AdaptivePolicyConfig(context_budget_tokens=500, target_utilization=0.8)
        )
        result = policy.evaluate(
            "fit context",
            [
                self.signal("high", token_count=220, relevance=1.0, exact_required=True),
                self.signal("low", token_count=400, relevance=0.45, trust=0.8),
            ],
        )
        by_id = {row["identity"]: row for row in result["decisions"]}
        self.assertEqual(by_id["high"]["recommended_action"], "KEEP")
        self.assertIn(by_id["low"]["recommended_action"], {"COMPRESS", "EXTERNALIZE"})
        self.assertIn("BUDGET_PRESSURE", by_id["low"]["reason_codes"])
        self.assertTrue(result["metrics"]["budget_fit"])

    def test_receipt_is_deterministic_and_timestamp_free(self) -> None:
        signals = [self.signal("b", relevance=0.6), self.signal("a", relevance=0.9)]
        first = self.policy.evaluate("deterministic task", signals)
        second = self.policy.evaluate("deterministic task", reversed(signals))
        self.assertEqual(first["receipt"]["receipt_hash"], second["receipt"]["receipt_hash"])
        self.assertEqual(first["decisions"], second["decisions"])
        self.assertNotIn("timestamp", str(first["receipt"]).casefold())

    def test_duplicate_signal_identity_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.policy.evaluate("duplicate", [self.signal("a"), self.signal("a")])

    def test_reference_metadata_rejects_payload_keys_recursively(self) -> None:
        with self.assertRaises(ValueError):
            self.signal("bad", metadata={"nested": {"payload": "forbidden"}})
        with self.assertRaises(ValueError):
            self.signal("bad2", metadata={"text": "forbidden"})

    def test_context_pack_adapter_never_carries_exact_text(self) -> None:
        item = ContextPackItem(
            tier="mandatory",
            kind="definition",
            path="syntavra_runtime/example.py",
            start_line=10,
            end_line=20,
            text="THIS EXACT PAYLOAD MUST NOT ENTER POLICY SIGNAL",
            tokens=42,
            token_confidence="exact",
            file_hash="a" * 64,
            reason="exact definition",
        )
        pack = TaskContextPack(
            query="example",
            budget_tokens=1000,
            used_tokens=42,
            seed_symbols=("Example.run",),
            items=(item,),
            affected_paths=(item.path,),
            affected_tests=(),
            required_verifiers=(),
            recoverable_paths=(),
            pack_hash="b" * 64,
        )
        signals = AdaptiveContextPolicy.signals_from_context_pack(pack)
        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0].exact_required)
        self.assertNotIn("THIS EXACT PAYLOAD", str(signals[0]))
        self.assertNotIn("text", signals[0].metadata)

    def test_multi_graph_adapter_preserves_reference_identity(self) -> None:
        result = {
            "candidates": [
                {
                    "identity": "item:abc",
                    "item_id": "abc",
                    "namespace_uri": "syntavra://context/item/abc",
                    "score": 12.0,
                    "estimated_tokens": 80,
                    "trust_levels": ["verified"],
                    "graph_kinds": ["code", "semantic"],
                    "layers": ["repository", "semantic"],
                    "evidence_refs": ["evidence:1"],
                }
            ]
        }
        signals = AdaptiveContextPolicy.signals_from_multi_graph(result)
        self.assertEqual(signals[0].identity, "item:abc")
        self.assertEqual(signals[0].item_id, "abc")
        self.assertEqual(signals[0].source_refs, ("evidence:1",))
        self.assertEqual(signals[0].token_count, 80)

    def test_optimization_mode_adapter_uses_existing_context_budget(self) -> None:
        config = AdaptivePolicyConfig.from_optimization_mode(MODES["ultra"])
        self.assertEqual(config.context_budget_tokens, MODES["ultra"].context_budget_tokens)

    def test_status_declares_no_storage_payload_or_side_effect_authority(self) -> None:
        status = self.policy.status()
        self.assertFalse(status["payload_authority"])
        self.assertFalse(status["persistent_store"])
        self.assertFalse(status["side_effects"])
        self.assertTrue(status["shadow_mode_supported"])
        self.assertEqual(
            set(status["item_actions"]),
            {"KEEP", "SUMMARIZE", "COMPRESS", "EXTERNALIZE", "ABSTAIN"},
        )
        self.assertEqual(set(status["session_actions"]), {"KEEP", "RESET", "BRANCH", "ABSTAIN"})


if __name__ == "__main__":
    unittest.main()
