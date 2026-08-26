from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.contract_version_graph import RuntimeContractVersionGraph
from tools.certify_runtime_contract_version_graph_v1 import certify

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/python/runtime-contract-version-graph-v1.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class RuntimeContractVersionGraphV1Tests(unittest.TestCase):
    def test_repository_graph_is_deterministic_and_contains_core_python_contracts(self) -> None:
        graph = RuntimeContractVersionGraph(ROOT)
        first = graph.build()
        second = graph.build()
        self.assertEqual(first, second)
        self.assertEqual(first["claim"], "RUNTIME_CONTRACT_VERSION_GRAPH_V1")
        self.assertGreaterEqual(first["node_count"], 20)
        self.assertGreater(first["edge_count"], 0)
        self.assertEqual(len(first["graph_sha256"]), 64)
        paths = {node["path"] for node in first["nodes"]}
        self.assertIn("contracts/python/capability-completeness-registry-v1.json", paths)
        self.assertIn("contracts/python/python-completion-certificate-v1.json", paths)
        self.assertIn("contracts/python/runtime-contract-version-graph-v1.json", paths)

    def test_transitive_dependency_invalidation_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _write(repo / "contracts/python/a.json", {"schema_version": 1, "family": "a", "authority": {"b": "contracts/python/b.json"}})
            _write(repo / "contracts/python/b.json", {"schema_version": 1, "family": "b", "authority": {"c": "contracts/python/c.json"}})
            _write(repo / "contracts/python/c.json", {"schema_version": 1, "family": "c", "value": 1})
            graph = RuntimeContractVersionGraph(repo)
            before = graph.build()
            _write(repo / "contracts/python/c.json", {"schema_version": 2, "family": "c", "value": 2})
            after = graph.build()
            plan = RuntimeContractVersionGraph.invalidation_plan(before, after)
            self.assertEqual(plan["changed_contracts"], ["contracts/python/c.json"])
            self.assertEqual(plan["invalidated_contracts"], ["contracts/python/a.json", "contracts/python/b.json", "contracts/python/c.json"])
            self.assertEqual(len(plan["invalidation_sha256"]), 64)

    def test_missing_contract_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _write(repo / "contracts/python/a.json", {"schema_version": 1, "family": "a", "authority": {"missing": "contracts/python/missing.json"}})
            with self.assertRaisesRegex(FileNotFoundError, "missing contract dependency"):
                RuntimeContractVersionGraph(repo).build()

    def test_cycles_do_not_loop_and_invalidate_the_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _write(repo / "contracts/python/a.json", {"schema_version": 1, "family": "a", "authority": {"b": "contracts/python/b.json"}})
            _write(repo / "contracts/python/b.json", {"schema_version": 1, "family": "b", "authority": {"a": "contracts/python/a.json"}})
            graph = RuntimeContractVersionGraph(repo)
            before = graph.build()
            _write(repo / "contracts/python/a.json", {"schema_version": 2, "family": "a", "authority": {"b": "contracts/python/b.json"}})
            after = graph.build()
            plan = RuntimeContractVersionGraph.invalidation_plan(before, after)
            self.assertEqual(plan["invalidated_contracts"], ["contracts/python/a.json", "contracts/python/b.json"])

    def test_self_evidence_reference_does_not_create_self_dependency_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _write(repo / "contracts/python/a.json", {"schema_version": 1, "family": "a", "implementation_evidence": ["contracts/python/a.json"]})
            snapshot = RuntimeContractVersionGraph(repo).build()
            self.assertEqual(snapshot["node_count"], 1)
            self.assertEqual(snapshot["edge_count"], 0)

    def test_contract_declares_metadata_only_python_ownership(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["claim"], "RUNTIME_CONTRACT_VERSION_GRAPH_V1")
        self.assertTrue(contract["strict"])
        self.assertTrue(contract["ownership_policy"]["metadata_only"])
        self.assertTrue(contract["ownership_policy"]["no_public_cli_route"])
        self.assertTrue(contract["ownership_policy"]["rust_feature_work_forbidden"])

    def test_exact_head_certifier_passes_without_resuming_rust(self) -> None:
        report = certify(ROOT)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["admission_ready"])
        self.assertFalse(report["rust_resume_allowed"])
        self.assertGreaterEqual(report["graph"]["node_count"], 20)
        self.assertGreater(report["graph"]["edge_count"], 0)


if __name__ == "__main__":
    unittest.main()
