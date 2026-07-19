from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalcore_runtime.memory import PersistentMemory
from signalcore_runtime.structural import StructuralIndex

class StructuralMemoryTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_incremental_python_index_and_impact(self):
        (self.root/"a.py").write_text("def target(x):\n    return x\n\ndef caller():\n    return target(1)\n"); index=StructuralIndex(self.root/"state.sqlite3",repository_root=self.root,repository_id="r"); self.assertEqual(index.index()["changed"],1); self.assertEqual(index.index()["reused"],1); self.assertEqual(index.inspect_symbol("target")["symbols"][0]["name"],"target"); self.assertTrue(any(row["source_symbol"]=="caller" for row in index.inspect_impact("target")["direct_references"]))
    def test_hash_invalidation_not_mtime_only(self):
        file=self.root/"a.py"; file.write_text("def a(): return 1\n"); index=StructuralIndex(self.root/"state.sqlite3",repository_root=self.root,repository_id="r"); index.index(); file.write_text("def b(): return 2\n"); self.assertEqual(index.index()["changed"],1); self.assertTrue(index.inspect_symbol("b")["symbols"])
    def test_memory_dedup_scope_and_supersession(self):
        memory=PersistentMemory(self.root/"memory.sqlite3",project_id="p",user_id="u"); first=memory.add("decision","Use SQLite WAL"); duplicate=memory.add("decision","Use SQLite WAL"); self.assertEqual(first.memory_id,duplicate.memory_id); new=memory.add("decision","Use SQLite WAL with busy timeout"); memory.supersede(first.memory_id,new.memory_id); results=memory.search("SQLite",include_superseded=False)["results"]; self.assertEqual(len(results),1); self.assertEqual(results[0]["memory_id"],new.memory_id)

if __name__ == "__main__": unittest.main()
