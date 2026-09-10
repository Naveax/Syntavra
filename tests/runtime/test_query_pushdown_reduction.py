from __future__ import annotations

import hashlib
import unittest

from syntavra_runtime.agent_retrieval import QueryPushdownEngine


class Graph:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limits: list[int] = []

    def query(self, query: str, *, limit: int = 20):
        self.limits.append(limit)
        return [dict(row, query_seen=query) for row in self.rows[:limit]]


class Capture:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.metadata: list[dict] = []

    def __call__(self, raw: bytes, metadata):
        self.payloads.append(bytes(raw))
        self.metadata.append(dict(metadata))
        digest = hashlib.sha256(raw).hexdigest()
        return {"artifact_id": "capture-" + digest[:24], "exact_handle": "sc://sha256/" + digest}


class QueryPushdownReductionTests(unittest.TestCase):
    @staticmethod
    def _rows(count: int = 12):
        return [
            {
                "node_id": f"n{index}",
                "name": f"symbol{index}",
                "qualified_name": f"pkg.symbol{index}",
                "path": f"src/{index % 3}/module_{index}.py",
                "kind": "function" if index % 2 == 0 else "class",
                "language": "python",
                "start_line": index * 10 + 1,
                "end_line": index * 10 + 5,
                "score": float(index) / 10.0,
                "query_backend": "graph",
                "metadata_json": "SECRET_INTERNAL_" + ("x" * 5000),
            }
            for index in range(count)
        ]

    def test_unknown_operator_and_fields_fail_before_graph_access(self) -> None:
        graph = Graph(self._rows())
        engine = QueryPushdownEngine(graph)
        capture = Capture()
        with self.assertRaises(ValueError):
            engine.reduce("symbol", operator="sql", capture=capture)
        with self.assertRaises(ValueError):
            engine.reduce("symbol", operator="group", group_by="metadata_json", capture=capture)
        with self.assertRaises(ValueError):
            engine.reduce("symbol", operator="sum", field="metadata_json", capture=capture)
        self.assertEqual(graph.limits, [])
        self.assertEqual(capture.payloads, [])

    def test_lossy_reduction_requires_exact_capture(self) -> None:
        graph = Graph(self._rows())
        engine = QueryPushdownEngine(graph)
        with self.assertRaises(ValueError):
            engine.reduce("symbol", operator="count", capture=None)  # type: ignore[arg-type]
        self.assertEqual(graph.limits, [])

    def test_count_group_numeric_sort_topk_and_sample(self) -> None:
        rows = self._rows()
        engine = QueryPushdownEngine(Graph(rows), max_limit=20, default_limit=5)

        count_capture = Capture()
        count = engine.reduce("symbol", operator="count", capture=count_capture, filters={"language": "python"})
        self.assertEqual(count.value, {"count": 12})
        self.assertTrue(count.source_window_complete)
        self.assertTrue(count.exact_artifact_id.startswith("capture-"))
        self.assertTrue(count.exact_handle.startswith("sc://sha256/"))

        group = engine.reduce("symbol", operator="group", group_by="kind", capture=Capture(), limit=4)
        self.assertEqual(group.value, ({"value": "class", "count": 6}, {"value": "function", "count": 6}))

        total = engine.reduce("symbol", operator="sum", field="score", capture=Capture())
        self.assertAlmostEqual(total.value["value"], sum(float(row["score"]) for row in rows))
        minimum = engine.reduce("symbol", operator="min", field="start_line", capture=Capture())
        maximum = engine.reduce("symbol", operator="max", field="end_line", capture=Capture())
        self.assertEqual(minimum.value["value"], 1.0)
        self.assertEqual(maximum.value["value"], 115.0)

        sorted_rows = engine.reduce(
            "symbol",
            operator="sort",
            field="score",
            fields=["name", "score"],
            capture=Capture(),
            limit=3,
        )
        self.assertEqual([row["name"] for row in sorted_rows.value], ["symbol0", "symbol1", "symbol2"])
        top = engine.reduce(
            "symbol",
            operator="top_k",
            field="score",
            fields=["name", "score"],
            capture=Capture(),
            limit=3,
        )
        self.assertEqual([row["name"] for row in top.value], ["symbol11", "symbol10", "symbol9"])

        sample_a = engine.reduce(
            "symbol",
            operator="sample",
            fields=["name", "path"],
            capture=Capture(),
            limit=4,
            sample_seed="frozen-seed",
        )
        sample_b = engine.reduce(
            "symbol",
            operator="sample",
            fields=["name", "path"],
            capture=Capture(),
            limit=4,
            sample_seed="frozen-seed",
        )
        self.assertEqual(sample_a.value, sample_b.value)

    def test_forbidden_raw_metadata_is_captured_locally_but_never_projected(self) -> None:
        capture = Capture()
        engine = QueryPushdownEngine(Graph(self._rows()), max_limit=20)
        reduced = engine.reduce(
            "symbol",
            operator="top_k",
            field="score",
            fields=["name", "path", "score"],
            capture=capture,
            limit=5,
        )
        self.assertIn(b"SECRET_INTERNAL_", capture.payloads[0])
        rendered = repr(reduced.value)
        self.assertNotIn("SECRET_INTERNAL_", rendered)
        self.assertNotIn("metadata_json", rendered)

    def test_window_completeness_is_explicit_when_graph_hits_cap(self) -> None:
        capture = Capture()
        engine = QueryPushdownEngine(Graph(self._rows(80)), max_limit=20)
        reduced = engine.reduce("symbol", operator="count", capture=capture)
        self.assertFalse(reduced.source_window_complete)
        self.assertEqual(reduced.source_window_limit, 20)
        self.assertEqual(reduced.value, {"count": 20})


if __name__ == "__main__":
    unittest.main()
