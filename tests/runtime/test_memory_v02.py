from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.memory import PersistentMemory


class MemoryV02Tests(unittest.TestCase):
    def test_graph_relation_and_ranked_search(self):
        with tempfile.TemporaryDirectory() as temp:
            memory = PersistentMemory(Path(temp) / "memory.sqlite3", project_id="p", user_id="u")
            decision = memory.add("decision", "Use SQLite WAL for durable jobs", confidence=0.95, tags=("runtime",))
            evidence = memory.add("evidence", "WAL survives concurrent readers", confidence=0.9)
            memory.link(decision.memory_id, "supported-by", evidence.memory_id, weight=2.0)
            neighbors = memory.neighbors(decision.memory_id)
            self.assertEqual(neighbors[0]["memory"]["memory_id"], evidence.memory_id)
            results = memory.search("SQLite durable", limit=5)["results"]
            self.assertEqual(results[0]["memory_id"], decision.memory_id)
            self.assertIn("score", results[0])


if __name__ == "__main__":
    unittest.main()
