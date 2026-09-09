from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.memory import PersistentMemory
from syntavra_runtime.zero_token_memory import ZeroTokenMemoryEngine


class ZeroTokenMemoryEngineTests(unittest.TestCase):
    def _engine(self, root: Path, *, project_id: str = "project", user_id: str = "user") -> ZeroTokenMemoryEngine:
        memory = PersistentMemory(root / "memory.sqlite3", project_id=project_id, user_id=user_id)
        evidence = EvidenceStore(root / "evidence", project_id=project_id)
        return ZeroTokenMemoryEngine(memory, evidence)

    def test_exact_raw_authority_and_idempotent_exact_dedup(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self._engine(Path(temp))
            first = engine.ingest("note", "  exact value  ", tags=("b", "a", "a"))
            duplicate = engine.ingest("note", "  exact value  ", tags=("a", "b"))
            raw_distinct = engine.ingest("note", "exact value", tags=("a", "b"))

            self.assertEqual(first["record"]["text"], "  exact value  ")
            self.assertEqual(first["record"]["memory_id"], duplicate["record"]["memory_id"])
            self.assertNotEqual(first["record"]["memory_id"], raw_distinct["record"]["memory_id"])
            for receipt in (first, duplicate, raw_distinct):
                self.assertEqual(receipt["provider_calls"], 0)
                self.assertEqual(receipt["provider_input_tokens"], 0)
                self.assertEqual(receipt["provider_output_tokens"], 0)
                self.assertEqual(receipt["provider_tokens"], 0)

    def test_shared_evidence_dedup_references_and_ttl_gc(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self._engine(Path(temp))
            now = time.time()
            first = engine.ingest(
                "event",
                "expired memory",
                expires_at=now - 1,
                evidence_data=b"shared exact evidence",
            )
            second = engine.ingest(
                "event",
                "live memory",
                expires_at=now + 20,
                evidence_data=b"shared exact evidence",
            )

            self.assertEqual(first["evidence_handle"], second["evidence_handle"])
            initial = engine.evidence.stats()
            self.assertEqual(initial["objects"], 1)
            self.assertEqual(initial["references"], 2)

            maintenance = engine.maintain(now=now)
            self.assertEqual(maintenance["memory_deleted"], 1)
            self.assertEqual(maintenance["evidence_references_released"], 1)
            self.assertEqual(maintenance["evidence_gc"]["deleted"], 0)
            middle = engine.evidence.stats()
            self.assertEqual(middle["objects"], 1)
            self.assertEqual(middle["references"], 1)

            final_maintenance = engine.maintain(now=now + 30)
            self.assertEqual(final_maintenance["memory_deleted"], 1)
            self.assertEqual(final_maintenance["evidence_references_released"], 1)
            self.assertEqual(final_maintenance["evidence_gc"]["deleted"], 1)
            final = engine.evidence.stats()
            self.assertEqual(final["objects"], 0)
            self.assertEqual(final["references"], 0)

    def test_expired_supersession_target_reactivates_authoritative_predecessor(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self._engine(Path(temp))
            now = time.time()
            old = engine.memory.add("decision", "stable predecessor")
            new = engine.memory.add("decision", "ephemeral replacement", expires_at=now - 1)
            engine.memory.supersede(old.memory_id, new.memory_id)

            maintenance = engine.maintain(now=now)
            self.assertEqual(maintenance["memory_deleted"], 1)
            self.assertEqual(maintenance["reactivated_superseded"], 1)

            result = engine.search("stable predecessor")
            self.assertEqual(result["provider_tokens"], 0)
            self.assertEqual(result["results"][0]["memory_id"], old.memory_id)
            self.assertIsNone(result["results"][0]["superseded_by"])

    def test_rebuild_index_is_local_and_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self._engine(Path(temp))
            record = engine.memory.add("fact", "local reindex needle")
            receipt = engine.rebuild_index()
            self.assertEqual(receipt["provider_calls"], 0)
            self.assertEqual(receipt["provider_tokens"], 0)
            self.assertTrue(receipt["reindex"]["ok"])
            if engine.memory.fts_available:
                with engine.memory.state.read() as db:
                    count = db.execute(
                        "SELECT COUNT(*) FROM memories_fts WHERE memory_id=?", (record.memory_id,)
                    ).fetchone()[0]
                self.assertEqual(count, 1)

    def test_project_scope_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            memory = PersistentMemory(root / "memory.sqlite3", project_id="p1")
            evidence = EvidenceStore(root / "evidence", project_id="p2")
            with self.assertRaises(ValueError):
                ZeroTokenMemoryEngine(memory, evidence)


if __name__ == "__main__":
    unittest.main()
