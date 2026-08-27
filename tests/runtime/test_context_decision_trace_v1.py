from __future__ import annotations

import copy
import unittest

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
    ContextPolicyState,
)
from syntavra_runtime.context_decision_trace import (
    REQUIRED_TRACE_DECISIONS,
    ContextDecisionTrace,
)


class ContextDecisionTraceV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=1000))

    @staticmethod
    def signal(identity: str, **overrides: object) -> ContextPolicySignal:
        values: dict[str, object] = {
            "identity": identity,
            "token_count": 120,
            "relevance": 0.8,
            "trust": 0.95,
            "freshness": 1.0,
            "recoverable": True,
            "namespace_uri": f"syntavra://context/item/{identity}",
            "item_id": identity,
            "source_refs": (f"evidence:{identity}",),
        }
        values.update(overrides)
        return ContextPolicySignal(**values)  # type: ignore[arg-type]

    def trace(self, task: str, signals: list[ContextPolicySignal], *, state: ContextPolicyState | None = None):
        return ContextDecisionTrace.from_policy_result(self.policy.evaluate(task, signals, state=state))

    def test_trace_is_deterministic_across_signal_order(self) -> None:
        signals = [self.signal("b", relevance=0.6), self.signal("a", relevance=1.0)]
        first = ContextDecisionTrace.from_policy_result(self.policy.evaluate("trace task", signals))
        second = ContextDecisionTrace.from_policy_result(self.policy.evaluate("trace task", reversed(signals)))
        self.assertEqual(first["trace_hash"], second["trace_hash"])
        self.assertEqual(first["events"], second["events"])
        self.assertTrue(ContextDecisionTrace.verify(first))
        self.assertNotIn("timestamp", str(first).casefold())

    def test_keep_maps_to_include(self) -> None:
        trace = self.trace("include", [self.signal("a", relevance=1.0)])
        self.assertEqual(trace["events"][0]["recommended_decision"], "include")

    def test_summary_and_compression_map_to_compress(self) -> None:
        summary = self.trace("summary", [self.signal("a", relevance=0.55, trust=0.8, freshness=0.8)])
        compressed = self.trace("compress", [self.signal("a", relevance=0.25, trust=0.65, freshness=0.7)])
        self.assertEqual(summary["events"][0]["recommended_decision"], "compress")
        self.assertEqual(compressed["events"][0]["recommended_decision"], "compress")

    def test_externalize_maps_to_omit_and_preserves_references(self) -> None:
        trace = self.trace("omit", [self.signal("a", relevance=0.0, trust=0.2, freshness=0.2)])
        event = trace["events"][0]
        self.assertEqual(event["recommended_decision"], "omit")
        self.assertEqual(event["source_refs"], ("evidence:a",))
        self.assertEqual(event["namespace_uri"], "syntavra://context/item/a")

    def test_explicit_retrieval_event_is_reference_only_and_deterministic(self) -> None:
        base = self.trace("omit", [self.signal("a", relevance=0.0, trust=0.2, freshness=0.2)])
        first = ContextDecisionTrace.append_retrieval(
            base,
            identity="a",
            source_refs=("evidence:a", "file-hash:" + "a" * 64),
            namespace_uri="syntavra://context/item/a",
            item_id="a",
            visible_tokens=80,
        )
        second = ContextDecisionTrace.append_retrieval(
            base,
            identity="a",
            source_refs=("file-hash:" + "a" * 64, "evidence:a"),
            namespace_uri="syntavra://context/item/a",
            item_id="a",
            visible_tokens=80,
        )
        self.assertEqual(first["trace_hash"], second["trace_hash"])
        self.assertEqual(first["events"][-1]["recommended_decision"], "retrieve")
        self.assertEqual(first["events"][-1]["scope"], "retrieval")

    def test_reset_and_abstain_are_traced(self) -> None:
        reset = self.trace(
            "reset",
            [self.signal("a", token_count=10, relevance=1.0)],
            state=ContextPolicyState(current_context_tokens=970, reset_allowed=True),
        )
        self.assertEqual(reset["events"][-1]["recommended_decision"], "reset")
        abstain = self.trace("unsafe", [self.signal("a", security_denied=True, relevance=1.0)])
        self.assertEqual(abstain["events"][-1]["recommended_decision"], "abstain")

    def test_branch_is_traced_without_mislabeling_it_as_reset_or_omit(self) -> None:
        trace = self.trace(
            "branch",
            [self.signal("a")],
            state=ContextPolicyState(task_drift=0.9, branch_allowed=True),
        )
        self.assertEqual(trace["events"][-1]["recommended_decision"], "branch")

    def test_shadow_mode_preserves_recommended_vs_effective_semantics(self) -> None:
        trace = self.trace(
            "shadow reset",
            [self.signal("a", token_count=10, relevance=1.0)],
            state=ContextPolicyState(current_context_tokens=970, reset_allowed=True, shadow_mode=True),
        )
        session = trace["events"][-1]
        self.assertEqual(session["recommended_decision"], "reset")
        self.assertEqual(session["effective_decision"], "include")

    def test_policy_receipt_tamper_fails_closed(self) -> None:
        result = self.policy.evaluate("tamper", [self.signal("a")])
        result["receipt"]["decisions"][0]["visible_tokens"] += 1
        with self.assertRaises(ValueError):
            ContextDecisionTrace.from_policy_result(result)

    def test_trace_event_tamper_fails_closed(self) -> None:
        trace = self.trace("tamper trace", [self.signal("a")])
        mutated = copy.deepcopy(trace)
        mutated["events"][0]["recommended_decision"] = "omit"
        with self.assertRaises(ValueError):
            ContextDecisionTrace.verify(mutated)

    def test_required_roadmap_decision_vocabulary_is_present(self) -> None:
        self.assertEqual(
            REQUIRED_TRACE_DECISIONS,
            {"include", "omit", "compress", "retrieve", "reset", "abstain"},
        )

    def test_trace_does_not_copy_task_or_signal_metadata_payload(self) -> None:
        trace = self.trace("SECRET TASK TEXT SHOULD STAY IN POLICY RECEIPT", [self.signal("a")])
        text = str(trace)
        self.assertNotIn("SECRET TASK TEXT", text)
        self.assertNotIn("metadata", text)


if __name__ == "__main__":
    unittest.main()
