from __future__ import annotations

import copy
import unittest

from syntavra_runtime.context_namespace import ContextNamespaceAddress
from syntavra_runtime.multi_graph_retrieval import (
    GRAPH_KINDS,
    GraphEdge,
    GraphNode,
    MultiGraphRetrieval,
)


REPO_URI = ContextNamespaceAddress.repository(
    "syntavra-test",
    directory="syntavra_runtime",
    file="syntavra_runtime/example.py",
    symbol="Example.run",
).uri


def node(
    graph_kind: str,
    node_id: str,
    label: str,
    *,
    uri: str = "",
    item_id: str = "",
    trust: str = "verified",
    tainted: bool = False,
    metadata: dict | None = None,
) -> GraphNode:
    return GraphNode(
        graph_kind=graph_kind,
        node_id=node_id,
        label=label,
        node_type="symbol",
        namespace_uri=uri,
        item_id=item_id,
        evidence_refs=(f"evidence:{graph_kind}:{node_id}",),
        trust_level=trust,
        tainted=tainted,
        metadata=metadata or {},
    )


class MultiGraphRetrievalTests(unittest.TestCase):
    def test_graph_kind_contract_is_complete(self) -> None:
        self.assertEqual(
            GRAPH_KINDS,
            {"code", "semantic", "temporal", "causal", "entity", "task", "provenance", "security"},
        )

    def test_node_rejects_payload_authority_in_metadata(self) -> None:
        for key in ("content", "payload", "raw_text", "body", "secret"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "payload authority"):
                    node("code", key, "Example", metadata={key: "must not live here"})
        with self.assertRaisesRegex(ValueError, "payload authority"):
            node("code", "nested", "Example", metadata={"safe": {"content": "nope"}})

    def test_noncanonical_namespace_uri_fails_closed(self) -> None:
        with self.assertRaises((ValueError, TypeError)):
            node("code", "x", "Example", uri="syntavra://repo/a/../b")

    def test_duplicate_layer_and_duplicate_nodes_fail_closed(self) -> None:
        engine = MultiGraphRetrieval()
        first = node("code", "a", "Alpha")
        engine.add_layer("code", "code", [first])
        with self.assertRaisesRegex(ValueError, "already registered"):
            engine.add_layer("code", "code", [first])
        other = MultiGraphRetrieval()
        with self.assertRaisesRegex(ValueError, "duplicate node ids"):
            other.add_layer("dupe", "code", [first, first])

    def test_missing_edge_endpoint_fails_closed(self) -> None:
        engine = MultiGraphRetrieval()
        with self.assertRaisesRegex(ValueError, "endpoint missing"):
            engine.add_layer(
                "broken",
                "code",
                [node("code", "a", "Alpha")],
                [GraphEdge("a", "missing", "calls")],
            )

    def test_required_graph_missing_fails_closed(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "a", "Alpha")])
        with self.assertRaisesRegex(RuntimeError, "required graph layers unavailable"):
            engine.retrieve("alpha", required_graphs=("security",))
        with self.assertRaisesRegex(ValueError, "unknown required graph"):
            engine.retrieve("alpha", required_graphs=("made-up",))

    def test_same_context_identity_fuses_across_graphs(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "c", "payment verifier", uri=REPO_URI)])
        engine.add_layer("provenance", "provenance", [node("provenance", "p", "verified payment provenance", uri=REPO_URI)])
        result = engine.retrieve("payment verifier", required_graphs=("code", "provenance"))
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["graph_kinds"], ["code", "provenance"])
        self.assertEqual(result["candidates"][0]["namespace_uri"], REPO_URI)
        self.assertGreaterEqual(len(result["candidates"][0]["evidence_refs"]), 2)

    def test_item_id_is_stronger_fusion_identity_than_graph_node_id(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("entity", "entity", [node("entity", "e1", "customer account", item_id="ctx-1")])
        engine.add_layer("task", "task", [node("task", "t1", "customer account migration", item_id="ctx-1")])
        result = engine.retrieve("customer account")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["identity"], "item:ctx-1")
        self.assertEqual(result["candidates"][0]["graph_kinds"], ["entity", "task"])

    def test_security_deny_blocks_same_identity_fail_closed(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "code-secret", "credential rotation", uri=REPO_URI)])
        engine.add_layer(
            "security",
            "security",
            [node("security", "deny-secret", "credential policy", uri=REPO_URI, metadata={"disposition": "deny"})],
        )
        result = engine.retrieve("credential rotation")
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_identity_count"], 1)

    def test_tainted_identity_is_excluded_unless_explicitly_requested(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "x", "unsafe migration", tainted=True)])
        self.assertEqual(engine.retrieve("unsafe migration")["candidate_count"], 0)
        allowed = engine.retrieve("unsafe migration", include_tainted=True)
        self.assertEqual(allowed["candidate_count"], 1)

    def test_temporal_stale_signal_downranks_current_signal(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "time",
            "temporal",
            [
                node("temporal", "old", "deploy configuration", metadata={"freshness": "stale"}),
                node("temporal", "new", "deploy configuration", metadata={"freshness": "current"}),
            ],
        )
        result = engine.retrieve("deploy configuration")
        self.assertEqual([row["identity"] for row in result["candidates"][:2]], ["node:temporal:new", "node:temporal:old"])
        self.assertGreater(result["candidates"][0]["score"], result["candidates"][1]["score"])

    def test_task_graph_receives_intent_boost(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("entities", "entity", [node("entity", "e", "fix payment retry")])
        engine.add_layer("tasks", "task", [node("task", "t", "fix payment retry")])
        result = engine.retrieve("fix payment retry")
        self.assertEqual(result["candidates"][0]["identity"], "node:task:t")

    def test_graph_propagation_retrieves_related_node(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "causal",
            "causal",
            [
                node("causal", "symptom", "checkout timeout"),
                node("causal", "cause", "database pool exhaustion"),
            ],
            [GraphEdge("symptom", "cause", "caused-by", confidence=0.95, evidence_refs=("trace:42",))],
        )
        result = engine.retrieve("checkout timeout", max_hops=2)
        identities = [row["identity"] for row in result["candidates"]]
        self.assertIn("node:causal:cause", identities)
        cause = next(row for row in result["candidates"] if row["identity"] == "node:causal:cause")
        self.assertTrue(any(reason["kind"] == "graph" for reason in cause["reasons"]))

    def test_query_is_bounded(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("entities", "entity", [node("entity", f"n{i}", f"shared target {i}") for i in range(150)])
        result = engine.retrieve("shared target", limit=1000, max_hops=99)
        self.assertLessEqual(result["candidate_count"], 100)
        self.assertEqual(result["receipt"]["limit"], 100)
        self.assertEqual(result["receipt"]["max_hops"], 4)

    def test_receipt_is_deterministic_and_timestamp_free(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "a", "payment retry")], source_refs=("canonical-graph",))
        first = engine.retrieve("payment retry")
        second = engine.retrieve("payment retry")
        self.assertEqual(first, second)
        encoded = repr(first["receipt"]).casefold()
        self.assertNotIn("timestamp", encoded)
        self.assertEqual(len(first["receipt"]["receipt_hash"]), 64)

    def test_structural_snapshot_adapter_preserves_repository_identity(self) -> None:
        engine = MultiGraphRetrieval()
        snapshot = {
            "symbols": [
                {
                    "path": "syntavra_runtime/example.py",
                    "name": "run",
                    "qualified_name": "Example.run",
                    "kind": "method",
                    "line": 10,
                    "end_line": 14,
                    "confidence": 1.0,
                    "parser": "python-ast",
                },
                {
                    "path": "syntavra_runtime/helper.py",
                    "name": "helper",
                    "qualified_name": "helper",
                    "kind": "function",
                    "line": 2,
                    "end_line": 4,
                    "confidence": 0.95,
                    "parser": "python-ast",
                },
            ],
            "edges": [
                {
                    "source_path": "syntavra_runtime/example.py",
                    "source_symbol": "Example.run",
                    "edge_type": "calls",
                    "target": "helper",
                    "target_path": "syntavra_runtime/helper.py",
                    "line": 12,
                    "confidence": 0.9,
                }
            ],
        }
        layer = engine.add_structural_snapshot("structural", snapshot, repository_id="syntavra-test")
        self.assertEqual(layer.graph_kind, "code")
        self.assertEqual(len(layer.nodes), 2)
        self.assertEqual(len(layer.edges), 1)
        result = engine.retrieve("Example.run", required_graphs=("code",))
        self.assertTrue(result["candidates"][0]["namespace_uri"].startswith("syntavra://repo/"))
        self.assertTrue(any(row["identity"].startswith("uri:syntavra://") for row in result["candidates"]))

    def test_all_eight_graphs_can_participate_in_one_bounded_query(self) -> None:
        engine = MultiGraphRetrieval()
        for graph_kind in sorted(GRAPH_KINDS):
            engine.add_layer(
                graph_kind,
                graph_kind,
                [node(graph_kind, graph_kind, f"checkout repair {graph_kind}", item_id="shared-checkout")],
            )
        result = engine.retrieve("checkout repair", required_graphs=GRAPH_KINDS)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["graph_kinds"], sorted(GRAPH_KINDS))
        self.assertTrue(all(result["graph_coverage"][kind] == 1 for kind in GRAPH_KINDS))

    def test_status_proves_no_parallel_store_or_payload_authority(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer("code", "code", [node("code", "a", "Alpha")])
        status = engine.status()
        self.assertFalse(status["persistent_store"])
        self.assertFalse(status["payload_authority"])
        self.assertNotIn("database", status)
        self.assertNotIn("path", status)


if __name__ == "__main__":
    unittest.main()
