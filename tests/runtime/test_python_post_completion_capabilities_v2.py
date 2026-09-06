from __future__ import annotations

import copy
import json
import unittest
from dataclasses import dataclass
from pathlib import Path

from syntavra_runtime.post_completion_context import CacheInvalidationProvenance, CompressionSafetyClassifier, ContextBudgetExplanationPlanner, ContextDeltaCompiler, ContextLeakDetector, MinimumEvidenceSchema, PolicyConflictResolver, SemanticPreservationVerifier, SourceSpecificTrustCalibrator, TaskLocalContextTransaction
from syntavra_runtime.post_completion_evidence import EvidenceHashChain, EvidenceMutationJournal, EvidenceRetentionGCPolicy, MutationEvent, RecoveryHandleIntegrityProof, SecretAwareArtifactStore
from syntavra_runtime.post_completion_product import ContextQualitySLOGate, FeatureSurfaceBudget, InternalCapabilityComposition, NoSilentFallbackReceipt, ProductProfileCertification
from syntavra_runtime.post_completion_runtime import ActionDryRunSimulator, CrossHostAdapterConformanceSuite, FaultInjectionHarness, GoldenCorpusGenerator, LiveTaskReplayFixture, MemoryCorrectnessSuite, MultiAgentHandoffContractVerifier, PerformanceBudgetGate, PromptCacheStabilityGuard, ProviderCapabilityNegotiator, ReproducibilityCapsule, ToolDiscoveryDegradationMode, ToolSchemaCompatibilityFingerprint
from syntavra_runtime.post_completion_common import sha256_digest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "contracts/python/capability-completeness-registry-v2.json"


@dataclass(frozen=True)
class FakeHandle:
    kind: str
    locator: dict
    integrity: str
    exact: bool = True


@dataclass
class FakeRecord:
    artifact_id: str


class FakeArtifactStore:
    def __init__(self):
        self.rows = {}

    def put(self, value, **kwargs):
        artifact_id = sha256_digest(value if isinstance(value, bytes) else str(value))
        self.rows[artifact_id] = (value, kwargs)
        return FakeRecord(artifact_id)

    def read(self, artifact_id):
        value = self.rows[artifact_id][0]
        return value if isinstance(value, bytes) else str(value).encode()

    def verify(self, artifact_id=None):
        return {"ok": artifact_id in self.rows if artifact_id else True}


class FakeScanner:
    def redact(self, value):
        text = str(value)
        detected = "SECRET=" in text
        return text.replace("SECRET=", "<redacted>="), {"redacted": detected}


class FakeJournalStore:
    def verify_journal(self):
        return {"ok": True, "events": 2, "head_hash": "sha256:" + "a" * 64, "failures": []}

    def journal(self, *, item_id=None):
        return []


class PythonPostCompletionCapabilitiesV2Tests(unittest.TestCase):
    def test_registry_covers_exact_python_scope_through_280(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        numbers = [row["number"] for row in registry["capabilities"]]
        self.assertEqual(numbers, list(range(243, 271)) + list(range(276, 281)))
        self.assertEqual([row["number"] for row in registry["deferred_rust_transition"]], list(range(271, 276)))
        self.assertTrue(all(row["state"] in {"implemented", "certified"} for row in registry["capabilities"]))
        self.assertTrue(registry["policy"]["parallel_persistent_store_forbidden"])
        self.assertEqual(registry["policy"]["public_command_growth_default"], 0)

    def test_243_evidence_mutation_journal(self):
        journal = EvidenceMutationJournal(FakeJournalStore())
        event = journal.event(MutationEvent("supersede", "new", predecessor_ids=("old",), successor_id="new", reason="fresh evidence"))
        self.assertEqual(event["claim"], "EVIDENCE_MUTATION_EVENT_V1")
        self.assertTrue(journal.verify()["ok"])
        with self.assertRaises(ValueError):
            MutationEvent("revoke", "x")

    def test_244_recovery_handle_integrity_proof(self):
        data = b"line1\nline2\n"
        handle = FakeHandle("file-range", {"path": "a.txt", "start_line": 1, "end_line": 2}, sha256_digest(data))
        self.assertTrue(RecoveryHandleIntegrityProof.verify(handle, lambda kind, locator: data, expected_bounds={"path": "a.txt"})["ok"])
        tampered = FakeHandle(handle.kind, handle.locator, "sha256:" + "0" * 64)
        self.assertFalse(RecoveryHandleIntegrityProof.verify(tampered, lambda *_: data)["ok"])

    def test_245_task_local_context_transaction(self):
        tx = TaskLocalContextTransaction({"a": 1})
        tx.set("a", 2)
        tx.set("b", 3)
        result, receipt = tx.rollback(reason="verification failed")
        self.assertEqual(result, {"a": 1})
        self.assertEqual(receipt["outcome"], "ROLLBACK")
        tx2 = TaskLocalContextTransaction({"a": 1})
        tx2.set("a", 2)
        result2, receipt2 = tx2.commit()
        self.assertEqual(result2, {"a": 2})
        self.assertEqual(receipt2["outcome"], "COMMIT")

    def test_246_context_delta_compiler(self):
        report = ContextDeltaCompiler.compile({"a": 1, "b": 2}, {"a": 3, "c": 4}, exact_keys={"a"})
        self.assertEqual(report["removed"], ["b"])
        self.assertEqual(report["added"], {"c": 4})
        self.assertEqual(report["changed"]["a"]["mode"], "exact")

    def test_247_context_budget_explanation(self):
        report = ContextBudgetExplanationPlanner.plan(100, minimums={"system": 20, "repository": 20})
        self.assertTrue(report["ok"])
        self.assertEqual(sum(report["allocation"].values()), 100)
        self.assertEqual(ContextBudgetExplanationPlanner.plan(10, minimums={"system": 20})["decision"], "ABSTAIN")

    def test_248_policy_conflict_resolver_security_wins(self):
        report = PolicyConflictResolver.resolve({"model": {"network": True}, "global": {"network": True}, "task": {"network": True}, "security": {"network": False}})
        self.assertFalse(report["effective"]["network"])
        self.assertEqual(report["ownership"]["network"], "security")
        self.assertTrue(report["conflicts"])

    def test_249_minimum_evidence_schema(self):
        self.assertEqual(MinimumEvidenceSchema.evaluate("edit", {"target": "a"})["decision"], "VERIFY")
        self.assertEqual(MinimumEvidenceSchema.evaluate("delete", {"target": "a"})["decision"], "ABSTAIN")
        self.assertEqual(MinimumEvidenceSchema.evaluate("read", {"target": "a"})["decision"], "ALLOW")

    def test_250_source_specific_trust_calibration(self):
        self.assertEqual(SourceSpecificTrustCalibrator.calibrate("git", verified=True)["decision"], "ALLOW")
        self.assertEqual(SourceSpecificTrustCalibrator.calibrate("web", tainted=True)["decision"], "ABSTAIN")
        self.assertEqual(SourceSpecificTrustCalibrator.calibrate("alien")["decision"], "VERIFY")

    def test_251_cache_invalidation_provenance(self):
        previous = {"source_hash": "a", "repository_commit": "x", "dependency_hash": "d", "tool_version": "1", "policy_version": "1", "schema_fingerprint": "s"}
        current = dict(previous)
        current["tool_version"] = "2"
        report = CacheInvalidationProvenance.compare(previous, current)
        self.assertTrue(report["invalidate"])
        self.assertEqual(report["reasons"], ["tool_version"])

    def test_252_context_leak_detector(self):
        clean = ContextLeakDetector.inspect([{"scope": {"project_id": "p", "task_id": "t", "agent_id": "a"}, "content": "safe"}], project_id="p", task_id="t", agent_id="a")
        self.assertTrue(clean["ok"])
        leak = ContextLeakDetector.inspect([{"scope": {"project_id": "other"}, "content": "SECRET=value"}], project_id="p", task_id="t", agent_id="a")
        self.assertFalse(leak["ok"])
        self.assertEqual(leak["decision"], "ABSTAIN")

    def test_253_compression_safety_classes(self):
        self.assertEqual(CompressionSafetyClassifier.classify({"exact_required": True})["class"], "EXACT_ONLY")
        self.assertEqual(CompressionSafetyClassifier.classify({"machine_consumed": True})["class"], "STRUCTURAL_SAFE")
        self.assertEqual(CompressionSafetyClassifier.classify({"semantic_critical": True})["class"], "SEMANTIC_SAFE")
        self.assertEqual(CompressionSafetyClassifier.classify({})["class"], "LOSSY_ALLOWED")

    def test_254_semantic_preservation_verifier(self):
        source = "Do not delete src/a.py. Error E123. Limit 25."
        self.assertTrue(SemanticPreservationVerifier.verify(source, source)["ok"])
        self.assertFalse(SemanticPreservationVerifier.verify(source, "delete source")["ok"])

    def test_255_cross_host_conformance(self):
        row = {"decision": "ALLOW", "selected_capabilities": ["a"], "risk": "low", "exact_recovery": True, "fallback": None}
        report = CrossHostAdapterConformanceSuite.certify({"codex": row, "claude": dict(row)}, required_hosts=("codex", "claude"))
        self.assertTrue(report["ok"])
        self.assertFalse(report["live_certification_claimed"])

    def test_256_action_dry_run(self):
        report = ActionDryRunSimulator.simulate({"kind": "git", "preconditions": ["clean"], "satisfied": {"clean": True}, "side_effects": ["commit"]})
        self.assertEqual(report["decision"], "READY")
        self.assertEqual(ActionDryRunSimulator.simulate({"kind": "deploy", "irreversible": True})["decision"], "VERIFY")

    def test_257_fault_injection_harness_all_faults(self):
        for fault in FaultInjectionHarness.FAULTS:
            self.assertEqual(FaultInjectionHarness.apply(fault, "payload")["fault"], fault)

    def test_258_golden_corpus_generator(self):
        report = GoldenCorpusGenerator.generate([{"permitted": True, "task_type": "edit", "content": "SECRET=abc", "expected": "ok"}, {"permitted": False, "content": "private"}])
        self.assertEqual(len(report["fixtures"]), 1)
        self.assertNotIn("abc", report["fixtures"][0]["content"])
        self.assertEqual(report["rejected_indices"], [1])

    def test_259_live_task_replay_fixture(self):
        report = LiveTaskReplayFixture.from_receipt({"task_id": "t", "outcome": "PASS", "repository_commit": "abc", "policy_hash": "p", "evidence_handles": ["b", "a"], "verifier": "unit"})
        self.assertEqual(report["fixture"]["evidence_handles"], ["a", "b"])
        self.assertTrue(report["fixture"]["fixture_id"].startswith("sha256:"))

    def test_260_reproducibility_capsule(self):
        data = {"repository_commit": "abc", "policy_hash": "p", "provider_profile": "x", "tool_versions": {"python": "3"}, "fixtures": ["f"], "verifier": "u", "environment": {"os": "linux"}}
        report = ReproducibilityCapsule.build(data)
        self.assertTrue(report["ok"])
        self.assertTrue(report["capsule_hash"].startswith("sha256:"))

    def test_261_performance_budget_gate(self):
        self.assertTrue(PerformanceBudgetGate.evaluate({"correct": True, "cpu_ms": 10, "ram_mb": 20, "disk_mb": 1, "latency_ms": 50, "token_overhead": 10}, {"cpu_ms": 20, "ram_mb": 30, "disk_mb": 2, "latency_ms": 100, "token_overhead": 20})["ok"])
        self.assertFalse(PerformanceBudgetGate.evaluate({"correct": False}, {})["ok"])

    def test_262_memory_correctness_suite(self):
        report = MemoryCorrectnessSuite.evaluate([{"expected_relevant": True, "returned": True}, {"expected_relevant": False, "returned": False}])
        self.assertEqual(report["metrics"]["precision"], 1.0)
        self.assertEqual(report["metrics"]["recall"], 1.0)
        self.assertEqual(report["metrics"]["wrong_project_rejection"], 1.0)

    def test_263_tool_schema_compatibility_fingerprint(self):
        old = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        new = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
        report = ToolSchemaCompatibilityFingerprint.compare(old, new)
        self.assertTrue(report["breaking"])
        self.assertEqual(report["changed_fields"], ["x"])

    def test_264_tool_discovery_degradation_mode(self):
        catalog = [{"name": "git.status", "namespace": "git", "keywords": ["status"]}, {"name": "test.run", "namespace": "test", "keywords": ["pytest"]}]
        self.assertEqual(ToolDiscoveryDegradationMode.discover("git status", catalog)["tools"][0]["name"], "git.status")
        self.assertEqual(ToolDiscoveryDegradationMode.discover("run", [{"name": "a.run"}, {"name": "b.run"}])["decision"], "ABSTAIN")

    def test_265_provider_capability_negotiation(self):
        report = ProviderCapabilityNegotiator.negotiate({"context_window": 1000, "tools": True}, [{"id": "bad", "context_window": 500, "tools": True, "cost": 1}, {"id": "good", "context_window": 2000, "tools": True, "cost": 2}])
        self.assertEqual(report["selected"]["id"], "good")
        self.assertTrue(report["fallback_explicit"])

    def test_266_prompt_cache_stability_guard(self):
        old = {"content_hash": "a", "order": [1, 2], "metadata": {"x": 1}, "provider": "p", "model": "m", "semantic_hash": "same"}
        new = dict(old)
        new["order"] = [2, 1]
        report = PromptCacheStabilityGuard.compare(old, new)
        self.assertTrue(report["cache_bust"])
        self.assertTrue(report["unnecessary_cache_bust"])

    def test_267_multi_agent_handoff_verifier(self):
        handoff = {"task_id": "t", "repository_commit": "abc", "constraints": ["no-rust"], "completed_work": ["242"], "evidence_handles": [{"integrity": "sha256:" + "a" * 64, "locator": {"id": "x"}}]}
        self.assertTrue(MultiAgentHandoffContractVerifier.verify(handoff, expected_repository_commit="abc")["ok"])
        self.assertFalse(MultiAgentHandoffContractVerifier.verify(handoff, expected_repository_commit="def")["ok"])

    def test_268_secret_aware_artifact_store(self):
        backend = FakeArtifactStore()
        facade = SecretAwareArtifactStore(backend, FakeScanner())
        self.assertTrue(facade.put("safe")["ok"])
        with self.assertRaises(ValueError):
            facade.put("SECRET=abc")
        self.assertTrue(facade.put("SECRET=abc", secret_policy="encrypted-reference", encrypted_reference="sha256:" + "b" * 64)["ok"])

    def test_269_retention_gc_preserves_provenance(self):
        report = EvidenceRetentionGCPolicy.plan([{"item_id": "parent", "expired": True, "pinned": False}, {"item_id": "child", "expired": False, "pinned": False, "parent_item_ids": ["parent"]}, {"item_id": "old", "expired": True, "pinned": False}])
        self.assertIn("old", report["delete_candidates"])
        self.assertNotIn("parent", report["delete_candidates"])

    def test_270_evidence_hash_chain_tamper_detection(self):
        built = EvidenceHashChain.build([{"a": 1}, {"b": 2}])
        self.assertTrue(EvidenceHashChain.verify(built["events"])["ok"])
        tampered = copy.deepcopy(built["events"])
        tampered[1]["payload"]["b"] = 3
        self.assertFalse(EvidenceHashChain.verify(tampered)["ok"])

    def test_276_feature_surface_budget(self):
        self.assertTrue(FeatureSurfaceBudget.evaluate(before_commands=["a"], after_commands=["a"])["ok"])
        self.assertFalse(FeatureSurfaceBudget.evaluate(before_commands=["a"], after_commands=["a", "b"])["ok"])

    def test_277_internal_capability_composition(self):
        report = InternalCapabilityComposition.compose({"a": {"primitive": "context", "depends_on": []}, "b": {"primitive": "verify", "depends_on": ["a"]}})
        self.assertTrue(report["ok"])
        self.assertEqual(report["order"], ["a", "b"])
        self.assertFalse(InternalCapabilityComposition.compose({"a": {"primitive": "context", "depends_on": ["b"]}, "b": {"primitive": "verify", "depends_on": ["a"]}})["ok"])

    def test_278_product_profile_certification(self):
        report = ProductProfileCertification.certify({"minimal": {"tools": ["a"], "max_tools": 10}, "balanced": {"tools": ["a", "b"], "max_tools": 36}, "audit": {"tools": ["a", "b", "c"]}})
        self.assertTrue(report["ok"])
        self.assertFalse(report["live_external_certification_claimed"])

    def test_279_no_silent_fallback_receipt(self):
        report = NoSilentFallbackReceipt.build(cause="provider unavailable", selected_path="local", risk_before="high", risk_after="medium", evidence=["e1"])
        self.assertEqual(report["cause"], "provider unavailable")
        self.assertTrue(report["receipt_id"].startswith("sha256:"))

    def test_280_context_quality_slo_gate(self):
        pass_metrics = {"task_success_rate": .99, "critical_evidence_recall": 1.0, "context_precision": .9, "verifier_success_rate": 1.0, "unsafe_action_rate": 0.0, "token_savings": .5}
        self.assertEqual(ContextQualitySLOGate.evaluate(pass_metrics)["decision"], "RELEASE")
        fail = dict(pass_metrics)
        fail["critical_evidence_recall"] = .5
        self.assertEqual(ContextQualitySLOGate.evaluate(fail)["decision"], "BLOCK_RELEASE")


if __name__ == "__main__":
    unittest.main()
