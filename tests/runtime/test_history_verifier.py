from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.history import ImmutableHistory
from syntavra_runtime.verifier_graph import VerifierGraph

class HistoryVerifierTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_history_hash_chain_and_exact_expansion(self):
        history=ImmutableHistory(self.root/"history.sqlite3",session_id="s"); one=history.append("user",{"text":"hello"}); two=history.append("tool",{"value":2}); self.assertTrue(history.verify_chain()); summary=history.create_summary("two events",parent_ids=(),source_event_seqs=(one.seq,two.seq)); self.assertEqual([row["payload"] for row in history.expand_summary(summary)["events"]],[{"text":"hello"},{"value":2}])
    def test_verifier_binding_and_invalidation(self):
        graph=VerifierGraph(self.root/"verify.sqlite3"); result=graph.record(("pytest",),tree_hash="tree1",environment_hash="env",dependency_hash="dep",toolchain_hash="tool",success=True,exit_code=0,evidence_handle="sc://sha256/"+"a"*64,affected_paths=("a.py","test_a.py")); self.assertIsNotNone(graph.lookup(("pytest",),tree_hash="tree1",environment_hash="env",dependency_hash="dep",toolchain_hash="tool")); self.assertIsNone(graph.lookup(("pytest",),tree_hash="tree2",environment_hash="env",dependency_hash="dep",toolchain_hash="tool")); self.assertEqual(graph.invalidated_by(("a.py",))[0]["cache_key"],result.cache_key)

if __name__ == "__main__": unittest.main()
