from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.agent_retrieval import QueryPushdownEngine
from syntavra_runtime.agent_runtime import GatewayPatchProvider
from syntavra_runtime.autonomous_agent import AgentMode, AgentTask
from syntavra_runtime.model_gateway import SequenceModelGateway
from syntavra_runtime.project_model import ProjectModel


class Graph:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limits: list[int] = []

    def query(self, query: str, *, limit: int = 20):
        self.limits.append(limit)
        return [dict(row, query_seen=query) for row in self.rows[:limit]]

    def impact(self, node_id: str, *, max_depth: int = 6):
        return {"root": node_id, "impacted": [], "max_depth": max_depth}


class AgentRetrievalTests(unittest.TestCase):
    @staticmethod
    def _project(root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
            "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
            encoding="utf-8",
        )
        (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "tests").mkdir()
        return project

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

    def test_gateway_search_never_replays_raw_graph_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            graph = Graph([
                {
                    "node_id": "n1",
                    "name": "VALUE",
                    "path": "module.py",
                    "kind": "symbol",
                    "language": "python",
                    "start_line": 1,
                    "end_line": 1,
                    "metadata_json": "SECRET_INTERNAL_METADATA" * 1000,
                    "evidence_ref": "SECRET_INTERNAL_REF",
                }
            ])
            gateway = SequenceModelGateway(
                [
                    {"action": "search", "query": "VALUE", "fields": ["name", "path", "start_line", "end_line"]},
                    {"action": "inspect", "path": "module.py", "start_line": 1, "end_line": 1},
                    {
                        "action": "edit",
                        "rationale": "update value",
                        "edits": [
                            {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1}
                        ],
                    },
                ]
            )
            model = ProjectModel(project)
            provider = GatewayPatchProvider(gateway, project=project, graph=graph, project_model=model)
            proposal = provider.propose(
                AgentTask("change VALUE", model.primary_verifier().argv, mode=AgentMode.SAFE_AUTONOMOUS),
                {
                    "workspace": str(project),
                    "attempt": 1,
                    "semantic_results": graph.query("VALUE"),
                    "current_diff": "",
                    "changed_files": [],
                },
                None,
            )
            self.assertIn("+VALUE = 2", proposal.patch)
            self.assertTrue(provider.trace[0]["query_pushdown"])
            self.assertEqual(provider.trace[0]["projected_fields"], ["name", "path", "start_line", "end_line"])
            visible = "\n".join(item.text for item in provider.context_receipts)
            self.assertNotIn("SECRET_INTERNAL_METADATA", visible)
            self.assertNotIn("SECRET_INTERNAL_REF", visible)
            self.assertNotIn("metadata_json", visible)

    def test_gateway_unique_search_inspect_removes_one_provider_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            graph = Graph([
                {
                    "node_id": "n1",
                    "name": "VALUE",
                    "path": "module.py",
                    "kind": "symbol",
                    "language": "python",
                    "start_line": 1,
                    "end_line": 1,
                    "metadata_json": "SECRET_FUSED_INTERMEDIATE" * 1000,
                }
            ])
            gateway = SequenceModelGateway(
                [
                    {
                        "action": "search_inspect",
                        "query": "VALUE",
                        "fields": ["name", "path", "start_line", "end_line"],
                        "context_lines": 1,
                    },
                    {
                        "action": "edit",
                        "rationale": "update value",
                        "edits": [
                            {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1}
                        ],
                    },
                ]
            )
            model = ProjectModel(project)
            provider = GatewayPatchProvider(gateway, project=project, graph=graph, project_model=model)
            proposal = provider.propose(
                AgentTask("change VALUE", model.primary_verifier().argv, mode=AgentMode.SAFE_AUTONOMOUS),
                {
                    "workspace": str(project),
                    "attempt": 1,
                    "semantic_results": graph.query("VALUE"),
                    "current_diff": "",
                    "changed_files": [],
                },
                None,
            )
            self.assertIn("+VALUE = 2", proposal.patch)
            self.assertEqual([row["action"] for row in provider.trace], ["search_inspect", "edit"])
            self.assertEqual(len(provider.context_receipts), 2)
            self.assertEqual(provider.trace[0]["status"], "FUSED_EXACT")
            self.assertTrue(provider.trace[0]["fused"])
            self.assertFalse(provider.trace[0]["intermediate_rows_visible"])
            visible = "\n".join(item.text for item in provider.context_receipts)
            self.assertNotIn("SECRET_FUSED_INTERMEDIATE", visible)
            self.assertIn("VALUE = 1", visible)

    def test_gateway_ambiguous_search_inspect_requires_explicit_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._project(Path(temporary))
            (project / "other.py").write_text("VALUE = 3\n", encoding="utf-8")
            graph = Graph([
                {"node_id": "n1", "name": "VALUE", "path": "module.py", "start_line": 1, "end_line": 1},
                {"node_id": "n2", "name": "VALUE", "path": "other.py", "start_line": 1, "end_line": 1},
            ])
            gateway = SequenceModelGateway(
                [
                    {"action": "search_inspect", "query": "VALUE", "fields": ["name", "path"]},
                    {"action": "inspect", "path": "module.py", "start_line": 1, "end_line": 1},
                    {
                        "action": "edit",
                        "rationale": "update explicit target",
                        "edits": [
                            {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1}
                        ],
                    },
                ]
            )
            model = ProjectModel(project)
            provider = GatewayPatchProvider(gateway, project=project, graph=graph, project_model=model)
            proposal = provider.propose(
                AgentTask("change VALUE", model.primary_verifier().argv, mode=AgentMode.SAFE_AUTONOMOUS),
                {
                    "workspace": str(project),
                    "attempt": 1,
                    "semantic_results": [],
                    "current_diff": "",
                    "changed_files": [],
                },
                None,
            )
            self.assertIn("+VALUE = 2", proposal.patch)
            self.assertEqual([row["action"] for row in provider.trace], ["search_inspect", "inspect", "edit"])
            self.assertEqual(provider.trace[0]["status"], "AMBIGUOUS")
            self.assertFalse(provider.trace[0]["fused"])
            self.assertTrue(provider.trace[0]["intermediate_rows_visible"])


if __name__ == "__main__":
    unittest.main()
