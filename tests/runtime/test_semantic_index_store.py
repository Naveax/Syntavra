from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.semantic_index_store import SemanticIndexStore
from syntavra_runtime.semantic_indexes import SemanticIndexBundle, SemanticIndexEdge, SemanticIndexNode
from syntavra_runtime.semantic_intelligence import IncrementalCodeIntelligenceGraph


class SemanticIndexStoreTests(unittest.TestCase):
    @staticmethod
    def node(node_id: str, name: str, *, exact: bool = True) -> SemanticIndexNode:
        return SemanticIndexNode(
            node_id=node_id,
            path="main.future",
            kind="function",
            name=name,
            qualified_name=f"future::{name}",
            start_line=1,
            end_line=2,
            language="future-language",
            evidence_ref="sha256:index",
            metadata={"source": "test-index", "exact_semantic": exact},
        )

    @staticmethod
    def bundle(*nodes: SemanticIndexNode, commit: str = "a" * 40, digest: str = "1" * 64) -> SemanticIndexBundle:
        edges = ()
        if len(nodes) >= 2:
            edges = (
                SemanticIndexEdge(
                    source=nodes[0].node_id,
                    target=nodes[1].node_id,
                    edge_type="calls",
                    confidence=1.0,
                    evidence_ref="sha256:index",
                    metadata={"source": "test-index", "exact_semantic": True},
                ),
            )
        return SemanticIndexBundle(
            format="scip-json",
            source_sha256=digest,
            repository_commit=commit,
            nodes=tuple(nodes),
            edges=edges,
            diagnostics=(),
        )

    def test_fresh_import_is_exact_and_visible_in_graph_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            result = store.import_bundle(
                self.bundle(self.node("scip:a", "ignite")),
                index_path=index,
                current_commit="a" * 40,
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["stale"])
            self.assertEqual(result["evidence_status"], "exact")
            queried = graph.query("ignite")
            self.assertEqual(queried[0]["semantic_status"], "exact")
            self.assertEqual(store.stats()["semantic_index_nodes"], 1)

    def test_stale_index_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "commit mismatch"):
                store.import_bundle(
                    self.bundle(self.node("scip:a", "ignite"), commit="a" * 40),
                    index_path=index,
                    current_commit="b" * 40,
                )
            self.assertEqual(store.stats()["semantic_index_sources"], 0)

    def test_allowed_stale_index_is_downgraded_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            result = store.import_bundle(
                self.bundle(self.node("scip:a", "ignite"), self.node("scip:b", "target"), commit="a" * 40),
                index_path=index,
                current_commit="b" * 40,
                allow_stale=True,
            )
            self.assertTrue(result["stale"])
            self.assertEqual(result["evidence_status"], "candidate-stale")
            queried = graph.query("ignite")[0]
            self.assertEqual(queried["semantic_status"], "candidate")
            self.assertTrue(queried["metadata"]["stale_semantic_index"])
            self.assertEqual(store.stats()["stale_semantic_index_sources"], 1)

    def test_reimport_replaces_only_owned_rows_and_preserves_syntax_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            (repository / "local.py").write_text("def local_symbol():\n    return True\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            graph.index_repository(repository)
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            first = store.import_bundle(
                self.bundle(self.node("scip:old", "old_symbol"), digest="1" * 64),
                index_path=index,
                current_commit="a" * 40,
            )
            second = store.import_bundle(
                self.bundle(self.node("scip:new", "new_symbol"), digest="2" * 64),
                index_path=index,
                current_commit="a" * 40,
            )
            self.assertEqual(first["source_key"], second["source_key"])
            self.assertFalse(any(item["name"] == "old_symbol" for item in graph.query("old_symbol", limit=100)))
            self.assertTrue(any(item["name"] == "new_symbol" for item in graph.query("new_symbol", limit=100)))
            self.assertTrue(any(item["name"] == "local_symbol" for item in graph.query("local_symbol", limit=100)))
            self.assertEqual(store.stats()["semantic_index_sources"], 1)
            self.assertEqual(store.stats()["semantic_index_nodes"], 1)

    def test_imported_node_id_cannot_overwrite_unowned_local_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            (repository / "local.py").write_text("def local_symbol():\n    return True\n", encoding="utf-8")
            graph = IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            graph.index_repository(repository)
            local = graph.query("local_symbol")[0]
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "node id collision"):
                store.import_bundle(
                    self.bundle(self.node(local["node_id"], "attacker_controlled_name")),
                    index_path=index,
                    current_commit="a" * 40,
                )
            self.assertEqual(graph.query("local_symbol")[0]["name"], "local_symbol")

    def test_remove_source_removes_owned_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = IncrementalCodeIntelligenceGraph(root / "graph.sqlite3")
            store = SemanticIndexStore(root / "graph.sqlite3")
            index = root / "index.scip.json"
            index.write_text("{}", encoding="utf-8")
            imported = store.import_bundle(
                self.bundle(self.node("scip:a", "ignite")),
                index_path=index,
                current_commit="a" * 40,
            )
            removed = store.remove(imported["source_key"])
            self.assertTrue(removed["removed"])
            self.assertFalse(graph.query("ignite"))
            self.assertEqual(store.stats()["semantic_index_sources"], 0)


if __name__ == "__main__":
    unittest.main()
