from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.infinite_context import CONTEXT_TIERS, RecursiveExecutionEngine, RecursiveTask, UnboundedContextCoordinator
from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.public_proof import BetaReceipt, PublicProofGate, WORKLOADS
from syntavra_runtime.release_identity import ReleaseIdentity, VersionLockError
from syntavra_runtime.paired_benchmark import CodingCorpusPlanner, PairedSchedule, SuperiorityGate, default_arms
from syntavra_runtime.semantic_structure import GraphEdge, GraphNode, SemanticGraph
from syntavra_runtime.zero_friction import ZeroFrictionManager


class ReleaseIdentityTests(unittest.TestCase):
    def test_version_is_locked_to_001_prerelease(self) -> None:
        identity = ReleaseIdentity()
        self.assertEqual(identity.version, "0.0.1")
        self.assertEqual(identity.channel, "pre-release")
        identity.require_version("v0.0.1")
        with self.assertRaises(VersionLockError):
            identity.require_version("0.0.2")


class IntegrationAndProductTests(unittest.TestCase):
    def test_integration_targets_are_met(self) -> None:
        coverage = IntegrationMatrix.validate()
        self.assertTrue(coverage["ok"], coverage)
        self.assertGreaterEqual(coverage["providers"], 10)
        self.assertGreaterEqual(coverage["frameworks"], 15)
        self.assertGreaterEqual(coverage["hosts"], 18)
        self.assertGreaterEqual(coverage["automatic_hosts"], 14)

    def test_one_command_plan_is_reversible_and_under_sixty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            plan = ZeroFrictionManager(project).install_plan()
            self.assertTrue(plan.one_command)
            self.assertLess(plan.estimated_seconds, 60)
            self.assertTrue(all(item.reversible for item in plan.actions if item.action not in {"verify", "record"}))


class StructuralV2Tests(unittest.TestCase):
    def test_query_impact_and_affected_test(self) -> None:
        graph = SemanticGraph()
        graph.add_node(GraphNode("auth", "function", "auth.refresh", "src/auth.py", 10, 50, "python", "sc://auth", 0.9, ("security",)))
        graph.add_node(GraphNode("service", "class", "AuthService", "src/service.py", 1, 90, "python", "sc://service", 0.4))
        graph.add_node(GraphNode("test", "test", "test_refresh", "tests/test_auth.py", 1, 40, "python", "sc://test"))
        graph.add_edge(GraphEdge("service", "auth", "calls", evidence_ref="sc://edge/service-auth"))
        graph.add_edge(GraphEdge("test", "service", "calls", evidence_ref="sc://edge/test-service"))
        results = graph.query("auth refresh")
        self.assertEqual(results[0].node.node_id, "auth")
        impact = graph.impact("auth")
        self.assertTrue(impact["exact_evidence_complete"])
        self.assertEqual([row["node_id"] for row in impact["affected_tests"]], ["test"])
        self.assertTrue(graph.validate()["ok"])


class SignalBenchV2Tests(unittest.TestCase):
    def test_150_task_30_repetition_schedule(self) -> None:
        tasks = CodingCorpusPlanner.generate_slots()
        arms = default_arms()
        schedule = PairedSchedule(tasks, arms, repetitions=30)
        self.assertEqual(len(tasks), 150)
        self.assertEqual(schedule.count, 150 * len(arms) * 30)
        first = next(schedule.iter_runs())
        self.assertTrue(first.pair_key)

    def test_synthetic_corpus_cannot_open_superiority_claim(self) -> None:
        result = SuperiorityGate.evaluate([{"task_id": "x", "arm_id": "syntavra", "synthetic": True}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["claim"], "EXTERNAL_SUPERIORITY_NOT_PROVEN")


class InfiniteContextTests(unittest.TestCase):
    def test_all_context_tiers_are_bounded_without_restart(self) -> None:
        reports = UnboundedContextCoordinator.stress_tiers(active_budget=4096)
        self.assertEqual(tuple(row["tier_tokens"] for row in reports), CONTEXT_TIERS)
        for row in reports:
            self.assertTrue(row["within_budget"], row)
            self.assertTrue(row["all_referenced"], row)
            self.assertFalse(row["forced_restart"], row)
            self.assertEqual(row["history_tokens"], row["tier_tokens"])

    def test_temporal_current_truth_and_exact_manifest(self) -> None:
        coordinator = UnboundedContextCoordinator(active_budget=512)
        for _ in range(1, 20):
            coordinator.append_virtual_history(2048)
        plan = coordinator.plan("current critical history")
        manifest = coordinator.exact_recovery_manifest()
        self.assertLessEqual(plan.active_tokens, 512)
        self.assertFalse(plan.forced_restart)
        self.assertTrue(manifest["all_referenced"])

    def test_recursive_execution_deduplicates_and_reduces(self) -> None:
        engine = RecursiveExecutionEngine(workers=4)
        tasks = [RecursiveTask("a", 1), RecursiveTask("b", 2), RecursiveTask("a", 1)]
        result = engine.execute(tasks, lambda task: task.payload * 2, lambda rows: sum(row.output for row in rows))
        self.assertEqual(result["tasks_executed"], 2)
        self.assertEqual(result["duplicates_suppressed"], 1)
        self.assertEqual(result["reduced"], 6)


class PublicProofTests(unittest.TestCase):
    def test_workload_suite_has_twelve_families(self) -> None:
        self.assertEqual(len(WORKLOADS), 12)
        self.assertEqual(PublicProofGate.workload_manifest()["workload_count"], 12)

    def test_maturity_gate_rejects_missing_real_history(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        receipt = BetaReceipt("r", now.isoformat(), "repo", "user", "code-search", True, True, 10, False, synthetic=True)
        result = PublicProofGate.evaluate_beta([receipt], now=now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["claim"], "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
