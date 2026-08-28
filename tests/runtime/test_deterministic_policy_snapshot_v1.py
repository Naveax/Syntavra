from __future__ import annotations

import copy
import unittest
from pathlib import Path

from syntavra_runtime.adaptive_context_policy import (
    AdaptiveContextPolicy,
    AdaptivePolicyConfig,
    ContextPolicySignal,
)
from syntavra_runtime.context_decision_trace import ContextDecisionTrace
from syntavra_runtime.contract_version_graph import RuntimeContractVersionGraph
from syntavra_runtime.deterministic_policy_snapshot import (
    POLICY_CONTRACT_PATH,
    POLICY_RUNTIME_PATH,
    DeterministicPolicySnapshot,
)
from syntavra_runtime.util import canonical_json, sha256_bytes, sha256_file


ROOT = Path(__file__).resolve().parents[2]


class DeterministicPolicySnapshotV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AdaptivePolicyConfig(context_budget_tokens=1000)
        self.policy = AdaptiveContextPolicy(self.config)

    @staticmethod
    def signal(identity: str = "a") -> ContextPolicySignal:
        return ContextPolicySignal(
            identity=identity,
            token_count=120,
            relevance=0.9,
            trust=0.95,
            freshness=1.0,
            recoverable=True,
            namespace_uri=f"syntavra://context/item/{identity}",
            item_id=identity,
            source_refs=(f"evidence:{identity}",),
        )

    @staticmethod
    def reseal(snapshot: dict[str, object]) -> None:
        basis = {
            "schema_version": snapshot["schema_version"],
            "policy_family": snapshot["policy_family"],
            "contract_graph_schema_version": snapshot["contract_graph_schema_version"],
            "policy_contract": snapshot["policy_contract"],
            "policy_runtime": snapshot["policy_runtime"],
            "config": snapshot["config"],
        }
        snapshot["snapshot_hash"] = sha256_bytes(canonical_json(basis))

    def result_and_trace(self, task: str = "snapshot task"):
        result = self.policy.evaluate(task, [self.signal()])
        return result, ContextDecisionTrace.from_policy_result(result)

    def test_snapshot_is_deterministic_and_timestamp_free(self) -> None:
        first = DeterministicPolicySnapshot.capture(self.config)
        second = DeterministicPolicySnapshot.capture(self.config)
        self.assertEqual(first, second)
        self.assertTrue(DeterministicPolicySnapshot.verify(first))
        self.assertNotIn("timestamp", str(first).casefold())
        self.assertNotIn("observed_at", str(first).casefold())

    def test_config_change_changes_snapshot_identity(self) -> None:
        first = DeterministicPolicySnapshot.capture(self.config)
        second = DeterministicPolicySnapshot.capture(
            AdaptivePolicyConfig(context_budget_tokens=1200)
        )
        self.assertNotEqual(first["snapshot_hash"], second["snapshot_hash"])

    def test_contract_identity_matches_runtime_contract_version_graph_node(self) -> None:
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        graph = RuntimeContractVersionGraph(ROOT).build()
        node = next(row for row in graph["nodes"] if row["path"] == POLICY_CONTRACT_PATH)
        self.assertEqual(snapshot["policy_contract"]["sha256"], node["sha256"])
        self.assertEqual(snapshot["contract_graph_schema_version"], graph["schema_version"])

    def test_runtime_identity_matches_exact_adaptive_policy_implementation(self) -> None:
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        self.assertEqual(
            snapshot["policy_runtime"]["sha256"],
            sha256_file(ROOT / POLICY_RUNTIME_PATH),
        )

    def test_reference_only_binding_attaches_snapshot_without_copying_task_payload(self) -> None:
        secret_task = "SECRET TASK PAYLOAD MUST REMAIN INSIDE THE VERIFIED RECEIPT"
        result, trace = self.result_and_trace(secret_task)
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        binding = DeterministicPolicySnapshot.bind(
            snapshot,
            result,
            trace=trace,
            task_reference={
                "task_id": "task-001",
                "source_refs": ["evidence:a"],
                "namespace_uri": "syntavra://task/task-001",
            },
        )
        self.assertTrue(
            DeterministicPolicySnapshot.verify_binding(binding, result, trace=trace)
        )
        self.assertEqual(binding["policy_snapshot_hash"], snapshot["snapshot_hash"])
        self.assertEqual(binding["policy_receipt_hash"], result["receipt"]["receipt_hash"])
        self.assertEqual(binding["context_decision_trace_hash"], trace["trace_hash"])
        self.assertNotIn("SECRET TASK PAYLOAD", str(binding))

    def test_binding_without_trace_remains_deterministic(self) -> None:
        result, _ = self.result_and_trace()
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        first = DeterministicPolicySnapshot.bind(
            snapshot,
            result,
            task_reference={"task_id": "task-001"},
        )
        second = DeterministicPolicySnapshot.bind(
            snapshot,
            result,
            task_reference={"task_id": "task-001"},
        )
        self.assertEqual(first["binding_hash"], second["binding_hash"])
        self.assertEqual(first["context_decision_trace_hash"], "")
        self.assertTrue(DeterministicPolicySnapshot.verify_binding(first, result))

    def test_policy_receipt_tamper_fails_closed(self) -> None:
        result, _ = self.result_and_trace()
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        result["receipt"]["decisions"][0]["visible_tokens"] += 1
        with self.assertRaises(ValueError):
            DeterministicPolicySnapshot.bind(snapshot, result)

    def test_receipt_config_must_match_snapshot_config(self) -> None:
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        other = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=1200))
        result = other.evaluate("other config", [self.signal()])
        with self.assertRaises(ValueError):
            DeterministicPolicySnapshot.bind(snapshot, result)

    def test_mismatched_context_decision_trace_fails_closed(self) -> None:
        result, _ = self.result_and_trace("first")
        other_result = self.policy.evaluate("second", [self.signal("b")])
        other_trace = ContextDecisionTrace.from_policy_result(other_result)
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        with self.assertRaises(ValueError):
            DeterministicPolicySnapshot.bind(snapshot, result, trace=other_trace)

    def test_binding_tamper_fails_closed(self) -> None:
        result, trace = self.result_and_trace()
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        binding = DeterministicPolicySnapshot.bind(snapshot, result, trace=trace)
        mutated = copy.deepcopy(binding)
        mutated["task_reference"] = {"task_id": "changed"}
        with self.assertRaises(ValueError):
            DeterministicPolicySnapshot.verify_binding(mutated, result, trace=trace)

    def test_reference_payload_keys_are_forbidden(self) -> None:
        result, _ = self.result_and_trace()
        snapshot = DeterministicPolicySnapshot.capture(self.config)
        for key in ("content", "payload", "raw_text", "body", "secret", "text"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    DeterministicPolicySnapshot.bind(
                        snapshot,
                        result,
                        task_reference={key: "not allowed"},
                    )

    def test_resealed_forged_runtime_identity_fails_current_authority_verification(self) -> None:
        snapshot = copy.deepcopy(DeterministicPolicySnapshot.capture(self.config))
        snapshot["policy_runtime"]["sha256"] = "0" * 64
        self.reseal(snapshot)
        with self.assertRaises(ValueError):
            DeterministicPolicySnapshot.verify(snapshot)


if __name__ == "__main__":
    unittest.main()
