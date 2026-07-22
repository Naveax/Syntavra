from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.semantic_intelligence import IncrementalCodeIntelligenceGraph


class PythonSemanticResolutionTests(unittest.TestCase):
    def test_unique_top_level_call_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "module.py").write_text(
                "def helper():\n"
                "    return 1\n\n"
                "def run():\n"
                "    return helper()\n",
                encoding="utf-8",
            )
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            self.assertTrue(graph.index_repository(root)["ok"])
            helper = next(row for row in graph.query("helper") if row["name"] == "helper")
            impact = graph.impact(helper["node_id"])
            self.assertTrue(impact["exact_evidence"])
            self.assertFalse(impact["candidate_evidence_present"])

    def test_local_shadowing_prevents_exact_call_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "module.py").write_text(
                "def helper():\n"
                "    return 1\n\n"
                "def run(helper):\n"
                "    return helper()\n",
                encoding="utf-8",
            )
            graph = IncrementalCodeIntelligenceGraph(Path(temporary) / "graph.sqlite3")
            self.assertTrue(graph.index_repository(root)["ok"])
            helper = next(row for row in graph.query("helper") if row["name"] == "helper")
            impact = graph.impact(helper["node_id"])
            self.assertFalse(impact["exact_evidence"])
            self.assertTrue(impact["candidate_evidence_present"])


if __name__ == "__main__":
    unittest.main()
