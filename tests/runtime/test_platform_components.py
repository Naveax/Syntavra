from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.platform import (
    AdapterRegistry,
    ArtifactStore,
    CapabilitySecurity,
    ContextCompiler,
    ContextIRItem,
    IncrementalCodeIntelligenceGraph,
    OutputFirewall,
    SecretlessProviderGateway,
    SessionMemory,
    SyntavraPlatform,
)


class SyntavraPlatformTests(unittest.TestCase):
    def test_artifact_store_exact_query_and_deduplication(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            first = store.put("alpha\nERROR boom\nomega", kind="log")
            second = store.put("alpha\nERROR boom\nomega", kind="log")
            self.assertEqual(first.artifact_id, second.artifact_id)
            self.assertEqual(store.read(first.artifact_id), b"alpha\nERROR boom\nomega")
            query = store.query(first.artifact_id, mode="errors")
            self.assertIn("ERROR boom", query["view"])
            self.assertTrue(store.verify(first.artifact_id)["ok"])
            self.assertEqual(store.stats()["artifacts"], 1)

    def test_output_firewall_preserves_exact_and_reduces_noise(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            firewall = OutputFirewall(store)
            output = "\n".join(["test_ok ... ok"] * 300 + ["FAILED tests/test_x.py:42 assertion error"])
            receipt = firewall.capture("pytest", output, exit_code=1)
            self.assertTrue(receipt.exact_recovery)
            self.assertIn("FAILED", receipt.compact_view)
            self.assertLess(receipt.visible_bytes, receipt.original_bytes)
            self.assertEqual(store.read(receipt.artifact_id).decode(), output)

    def test_context_compiler_deduplicates_deltas_and_externalizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            compiler = ContextCompiler(store)
            large = "line\n" * 3000
            items = [
                ContextIRItem("sys", "system", "text", "system", "keep safe", 1.0, True),
                ContextIRItem("repo", "repository", "source", "src/a.py", large, 0.8, True),
                ContextIRItem("dup", "task", "text", "duplicate", "keep safe", 0.1),
                ContextIRItem("user", "user", "text", "user", "repair error", 1.0),
            ]
            pack = compiler.compile(items, provider="openai", budget_tokens=1000, externalize_threshold_bytes=100)
            self.assertTrue(pack.deterministic)
            self.assertTrue(pack.artifacts)
            self.assertLessEqual(pack.used_tokens, 1000)
            self.assertEqual(sum(item["content"] == "keep safe" for item in pack.items), 1)
            second = compiler.compile(items, provider="openai", budget_tokens=1000, externalize_threshold_bytes=100)
            self.assertEqual(pack.pack_hash, second.pack_hash)

    def test_incremental_graph_python_query_and_impact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "app.py").write_text("def helper():\n    return 1\n\ndef run():\n    return helper()\n", encoding="utf-8")
            (root / "test_app.py").write_text("from app import run\n\ndef test_run():\n    assert run() == 1\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            indexed = graph.index_repository(root)
            self.assertGreaterEqual(indexed["nodes"], 4)
            results = graph.query("helper")
            self.assertTrue(results)
            impact = graph.impact(results[0]["node_id"])
            self.assertTrue(impact["exact_evidence"])
            unchanged = graph.index_repository(root)
            self.assertEqual(unchanged["changed_files"], 0)
            self.assertEqual(unchanged["unchanged_files"], 2)

    def test_memory_exact_chain_views_fork_merge_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            memory = SessionMemory(Path(temporary) / "memory.sqlite3", project_id="repo")
            root = memory.open("root", metadata={"goal": "fix"})
            self.assertFalse(root["restored"])
            memory.append("root", "decision", {"decision": "keep version 0.0.1"})
            memory.append("root", "test-failure", {"error": "boom", "file": "app.py"})
            compacted = memory.compact("root")
            self.assertEqual(len(compacted["summaries"]), len(SessionMemory.VIEWS))
            self.assertEqual(len(SessionMemory.VIEWS), 10)
            self.assertTrue(memory.retrieve("root", "boom")["results"])
            checkpoint = memory.checkpoint("root", "safe")
            self.assertTrue(memory.restore(checkpoint["checkpoint_id"])["exact_recovery"])
            forked = memory.fork("root", label="branch")
            memory.append(forked["child"]["session_id"], "change", {"file": "app.py"})
            merged = memory.merge(("root", forked["child"]["session_id"]), label="merge")
            self.assertEqual(len(merged["parents"]), 2)
            self.assertTrue(memory.verify("root")["ok"])

    def test_capability_security_argument_binding_and_single_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            security = CapabilitySecurity(Path(temporary))
            denied = security.decide("terminal.exec", {"argv": ["rm", "-rf", "/"]}, sandboxed=True, user_authorized=True)
            self.assertFalse(denied.allowed)
            self.assertEqual(denied.reason, "destructive-command-denied")
            token = security.issue(
                session_id="s", tool="repo.patch", arguments={"path": "a.py"},
                resource="workspace:/a.py", permissions=("write",), ttl_seconds=60,
            )
            verified = security.verify(token, tool="repo.patch", arguments={"path": "a.py"}, resource="workspace:/a.py")
            self.assertTrue(verified["ok"])
            replay = security.verify(token, tool="repo.patch", arguments={"path": "a.py"}, resource="workspace:/a.py")
            self.assertFalse(replay["ok"])
            self.assertEqual(replay["reason"], "already-consumed")

    def test_secretless_gateway_removes_credentials(self):
        environment = {"PATH": "/bin", "OPENAI_API_KEY": "secret", "CUSTOM_TOKEN": "secret2", "SAFE": "yes"}
        sanitized = SecretlessProviderGateway.sanitize_environment(environment)
        self.assertEqual(sanitized, {"PATH": "/bin", "SAFE": "yes"})
        plan = SecretlessProviderGateway.plan("openai")
        self.assertFalse(plan["agent_environment_contains_secret"])
        self.assertEqual(plan["transport_injection"]["visibility"], "gateway-process-only")

    def test_non_cli_official_adapters_are_first_class(self):
        validation = AdapterRegistry.validate()
        self.assertTrue(validation["ok"])
        self.assertGreaterEqual(validation["non_cli_adapters"], 8)
        records = AdapterRegistry.records()
        self.assertTrue(any(row["surface"] == "ide" and not row["detection_commands"] for row in records))

    def test_reference_agent_and_runtime_doctor(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "repo"
            project.mkdir()
            (project / "main.py").write_text("def repair_target():\n    return True\n", encoding="utf-8")
            runtime = SyntavraPlatform(project, Path(temporary) / "state")
            runtime.graph.index_repository(project)
            plan = runtime.agent.plan("repair repair_target")
            self.assertEqual(plan["execution_mode"], "plan-only-until-authorized")
            self.assertTrue(plan["candidate_symbols"])
            self.assertTrue(runtime.doctor()["ok"])
            self.assertEqual(runtime.status()["version"], "0.0.1")


if __name__ == "__main__":
    unittest.main()
