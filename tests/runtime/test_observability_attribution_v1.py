from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from syntavra_runtime.observability_attribution import (
    AttributionPolicy,
    ObservabilityAttribution,
    PerformanceBudget,
    PerformanceSample,
    QualitySLO,
    QualitySample,
    RecoveryBudget,
    RecoverySample,
)
from syntavra_runtime.runtime_evidence import RuntimeEvidenceGraph
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger


class ObservabilityAttributionV1Tests(unittest.TestCase):
    def policy(self) -> AttributionPolicy:
        return AttributionPolicy(
            performance=PerformanceBudget(500.0, 250.0, 64 * 1024 * 1024, 8 * 1024 * 1024, 128),
            recovery=RecoveryBudget(2.0, True),
            quality=QualitySLO(1.0, 1.0, 1.0, 0),
        )

    def receipts(self, root: Path):
        ledger = UsageReceiptLedger(root / "usage.sqlite3")
        usage = ledger.record(
            task_id="task-1", arm_id="candidate", repetition=1, cache_mode="warm",
            provider="openai", request_id="request-1",
            provider_response={"usage": {"input_tokens": 20, "input_tokens_details": {"cached_tokens": 5}, "output_tokens": 3}},
            quota_cost=1.0, hardware_hash="a" * 64,
        ).receipt
        token = ledger.record_attribution(
            task_id="task-1", arm_id="candidate", repetition=1, session_id="session-1",
            provider="openai", model="fixture-model", request_id_hash=usage.request_id_hash,
            provider_receipt_hash=usage.receipt_hash,
            sources={"user_prompt": 8, "repository_context": 7, "assistant_output": 3},
            confidence={"user_prompt": "LOCALLY_TOKENIZED", "repository_context": "LOCALLY_TOKENIZED", "assistant_output": "PROVIDER_OBSERVED"},
            baseline_tokens=18, baseline_confidence="LOCALLY_TOKENIZED",
        )
        return usage, token

    def good_gate(self):
        return ObservabilityAttribution.evaluate(
            policy=self.policy(),
            performance=PerformanceSample(100.0, 50.0, 1024, 512, 16),
            recovery=RecoverySample(100, 125, True),
            quality_samples=[QualitySample(True, True, True, 0)],
        )

    def test_policy_snapshot_is_deterministic(self):
        policy = self.policy()
        self.assertEqual(policy.snapshot_hash, ObservabilityAttribution.policy_snapshot(policy))
        self.assertEqual(
            ObservabilityAttribution.policy_snapshot({"b": 2, "a": 1}),
            ObservabilityAttribution.policy_snapshot({"a": 1, "b": 2}),
        )

    def test_context_tool_policy_decisions_link_existing_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = RuntimeEvidenceGraph(root / "runtime-evidence.sqlite3")
            runtime = ObservabilityAttribution(graph)
            usage, token = self.receipts(root)
            gate = self.good_gate()
            receipts = []
            for kind, action, subject in (
                ("context", "include", "repository-context"),
                ("tool", "select", "read-file"),
                ("policy", "allow", "safe-action"),
            ):
                receipts.append(runtime.record_decision(
                    task_id="task-1", session_id="session-1", decision_kind=kind,
                    action=action, subject=subject, policy=self.policy(),
                    evidence_hashes=["b" * 64], usage_receipt=usage, token_receipt=token,
                    gate=gate, repository_commit="c" * 40,
                ))
            self.assertEqual({r.decision_kind for r in receipts}, {"context", "tool", "policy"})
            self.assertTrue(all(len(r.receipt_hash) == 64 for r in receipts))
            relations = {r["relation"]: r["count"] for r in graph.stats()["relations"]}
            self.assertEqual(relations["ATTRIBUTED_DECISION"], 3)
            self.assertEqual(relations["LINKED_PROVIDER_USAGE"], 3)
            self.assertEqual(relations["LINKED_TOKEN_ATTRIBUTION"], 3)
            self.assertEqual(relations["EVALUATED_BY"], 3)

    def test_receipt_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ObservabilityAttribution(RuntimeEvidenceGraph(root / "evidence.sqlite3"))
            usage, token = self.receipts(root)
            bad = replace(token, provider_receipt_hash="d" * 64)
            with self.assertRaisesRegex(ValueError, "does not reference provider usage receipt"):
                runtime.record_decision(
                    task_id="task-1", session_id="session-1", decision_kind="context",
                    action="include", subject="repo", policy=self.policy(),
                    usage_receipt=usage, token_receipt=bad,
                )

    def test_budget_recovery_and_quality_violations_fail_closed(self):
        gate = ObservabilityAttribution.evaluate(
            policy=self.policy(),
            performance=PerformanceSample(900.0, 400.0, 128 * 1024 * 1024, 16 * 1024 * 1024, 256),
            recovery=RecoverySample(100, 300, False),
            quality_samples=[QualitySample(False, False, False, 1)],
        )
        self.assertFalse(gate.ok)
        self.assertIn("latency-budget-exceeded", gate.performance_reasons)
        self.assertIn("recovery-amplification-exceeded", gate.recovery_reasons)
        self.assertIn("exact-recovery-required", gate.recovery_reasons)
        self.assertIn("task-success-slo-violated", gate.quality_reasons)
        self.assertIn("unsafe-action-slo-violated", gate.quality_reasons)

    def test_no_parallel_store_or_public_cli_surface(self):
        status = ObservabilityAttribution.status()
        self.assertTrue(status["runtime_evidence_graph_reused"])
        self.assertFalse(status["parallel_persistent_store"])
        self.assertFalse(status["provider_usage_store_duplicated"])
        self.assertFalse(status["token_attribution_store_duplicated"])
        self.assertFalse(status["public_cli_route"])


if __name__ == "__main__":
    unittest.main()
