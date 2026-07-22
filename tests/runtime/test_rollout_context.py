from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.context_governor import evaluate, stable_prefix_hash
from syntavra_runtime.rollout import RolloutTailer

class RolloutContextTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_incremental_partial_and_duplicate_events(self):
        rollout=self.root/"rollout.jsonl"; state=self.root/"state.json"; event={"event_id":"a","type":"response.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":3,"reasoning_tokens":2}}; encoded=json.dumps(event).encode(); rollout.write_bytes(encoded[:10]); self.assertEqual(RolloutTailer(rollout,state).poll()["processed_events"],0)
        with rollout.open("ab") as handle: handle.write(encoded[10:]+b"\n"+encoded+b"\n")
        second=RolloutTailer(rollout,state).poll(); self.assertEqual(second["processed_events"],1); self.assertEqual(second["counters"]["model_turns"],1); self.assertEqual(second["counters"]["duplicate_events"],1); self.assertEqual(second["counters"]["fresh_input_tokens"],6)
    def test_context_thresholds(self): self.assertEqual(evaluate(49,100).level,0); self.assertIn("externalize_evidence",evaluate(65,100).actions); self.assertTrue(evaluate(90,100).mandatory_split)
    def test_stable_prefix_is_order_independent(self): self.assertEqual(stable_prefix_hash([("b","2"),("a","1")]),stable_prefix_hash([("a","1"),("b","2")]))

if __name__ == "__main__": unittest.main()
