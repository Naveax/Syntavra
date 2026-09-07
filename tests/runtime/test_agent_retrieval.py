from __future__ import annotations

import unittest

from syntavra_runtime.agent_retrieval import QueryPushdownEngine


class Graph:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limits: list[int] = []

    def query(self, query: str, *, limit: int = 20):
        self.limits.append(limit)
        return [dict(row, query_seen=query) for row in self.rows[:limit]]


class AgentRetrievalTests(unittest.TestCase):
    def test_projection_drops_large_irrelevant_metadata(self) -> None:
        graph = Graph([
            {
                "node_id": "n1",
                "name": "target",
                "path": "src/a.py",
                "kind": "function",
                "language": "python",
                "start_line": 10,
                "end_line": 20,
                "metadata_json": "x" * 100_000,
                "evidence_ref": "secret-internal-ref",
            }
        ])
        engine = QueryPushdownEngine(graph)
        result = engine.search("target", limit=3, fields=["name", "path", "start_line", "end_line"])
        self.assertEqual(result.rows, ({"name": "target", "path": "src/a.py", "start_line": 10, "end_line": 20},))
        self.assertNotIn("metadata_json", result.rows[0])
        self.assertNotIn("evidence_ref", result.rows[0])
        self.assertLessEqual(graph.limits[-1], 20)

    def test_unknown_projection_or_filter_fails_closed(self) -> None:
        engine = QueryPushdownEngine(Graph([]))
        with self.assertRaises(ValueError):
            engine.search("x", fields=["raw_database_row"])
        with self.assertRaises(ValueError):
            engine.search("x", filters={"arbitrary_sql": "yes"})

    def test_top_k_and_local_filters_are_bounded(self) -> None:
        rows = [
            {"node_id": f"n{i}", "name": f"name{i}", "path": f"src/{i}.py", "kind": "function", "language": "python"}
            for i in range(50)
        ]
        engine = QueryPushdownEngine(Graph(rows), max_limit=20)
        result = engine.search("name", limit=3, filters={"language": "python"}, fields=["node_id", "path"])
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.limit, 3)
        self.assertLessEqual(result.raw_count, 6)

    def test_unique_result_fuses_search_and_ranged_read_without_intermediate_rows(self) -> None:
        graph = Graph([
            {
                "node_id": "n1",
                "name": "target",
                "path": "src/a.py",
                "kind": "function",
                "language": "python",
                "start_line": 20,
                "end_line": 30,
                "metadata_json": "x" * 100_000,
            }
        ])
        calls = []

        def reader(path, *, start_line, end_line, max_bytes):
            calls.append((path, start_line, end_line, max_bytes))
            return {
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "sha256": "a" * 64,
                "bytes": 1000,
                "truncated": False,
                "content": "def target():\n    return 1\n",
            }

        fused = QueryPushdownEngine(graph).search_inspect(
            "target",
            reader=reader,
            fields=["name", "path", "start_line", "end_line"],
            context_lines=4,
        )
        self.assertEqual(fused.status, "FUSED_EXACT")
        self.assertFalse(fused.intermediate_rows_visible)
        self.assertEqual(fused.candidates, ())
        self.assertIsNotNone(fused.source)
        self.assertEqual(calls[0][:3], ("src/a.py", 16, 34))

    def test_ambiguous_result_never_autopicks_a_file(self) -> None:
        graph = Graph([
            {"node_id": "n1", "name": "target", "path": "src/a.py", "start_line": 10, "end_line": 20},
            {"node_id": "n2", "name": "target", "path": "src/b.py", "start_line": 30, "end_line": 40},
        ])
        calls = []

        def reader(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("ambiguous fusion must not inspect a guessed target")

        fused = QueryPushdownEngine(graph).search_inspect("target", reader=reader, fields=["name", "path"])
        self.assertEqual(fused.status, "AMBIGUOUS")
        self.assertTrue(fused.intermediate_rows_visible)
        self.assertEqual(len(fused.candidates), 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
