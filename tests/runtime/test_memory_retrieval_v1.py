from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.memory_intelligence import (
    MEMORY_RETRIEVAL_KINDS,
    MemoryIntelligenceStore,
    MemoryRetrievalV1,
    MemoryScope,
)
from syntavra_runtime.session_memory import SessionMemory


class MemoryRetrievalV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = MemoryIntelligenceStore(root / "memory.sqlite3")
        self.sessions = SessionMemory(root / "session.sqlite3", project_id="syntavra")
        self.sessions.open("session-a")
        self.engine = MemoryRetrievalV1(self.store, session_memory=self.sessions)
        self.scope = MemoryScope(project_id="syntavra", user_id="naveax", session_id="session-a")

    def remember(self, text: str, *, kind: str = "semantic", **kwargs):
        return self.engine.remember(
            text,
            kind=kind,
            scope=kwargs.pop("scope", self.scope),
            provenance_refs=kwargs.pop("provenance_refs", ("evidence:test",)),
            **kwargs,
        )

    def test_all_memory_kinds_and_scope_isolation(self) -> None:
        self.assertEqual(
            MEMORY_RETRIEVAL_KINDS,
            {"episodic", "semantic", "procedural", "project", "user", "temporal"},
        )
        for kind in sorted(MEMORY_RETRIEVAL_KINDS):
            self.remember(f"{kind} alpha marker", kind=kind)

        other = MemoryScope(project_id="other-project", user_id="naveax")
        self.engine.remember(
            "semantic alpha marker from other project",
            kind="semantic",
            scope=other,
            provenance_refs=("evidence:other",),
        )
        result = self.engine.retrieve("alpha marker", scope=self.scope, include_session=False, limit=20)
        self.assertEqual({row["kind"] for row in result["results"]}, set(MEMORY_RETRIEVAL_KINDS))
        self.assertTrue(all(row["scope"]["project_id"] == "syntavra" for row in result["results"]))

    def test_provenance_is_required_and_dedupe_is_stable(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.remember("missing provenance", kind="semantic", scope=self.scope, provenance_refs=())
        first = self.remember("stable semantic fact")
        second = self.remember("stable semantic fact")
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertEqual(first["source_hash"], second["source_hash"])

    def test_conflict_is_explicit_and_never_silently_overridden(self) -> None:
        first = self.remember("The release policy allows alpha", kind="semantic")
        second = self.remember(
            "The release policy denies alpha",
            kind="semantic",
            conflicts_with=(first["memory_id"],),
        )
        recovered_first = self.engine.recover(first["memory_id"])
        recovered_second = self.engine.recover(second["memory_id"])
        self.assertIn(second["memory_id"], recovered_first["conflicts_with"])
        self.assertIn(first["memory_id"], recovered_second["conflicts_with"])
        result = self.engine.retrieve("release policy alpha", scope=self.scope, include_session=False)
        by_id = {row["memory_id"]: row for row in result["results"]}
        self.assertIn(first["memory_id"], by_id)
        self.assertIn(second["memory_id"], by_id)
        self.assertTrue(by_id[first["memory_id"]]["conflicts_with"])

    def test_supersession_and_forgetting_hide_active_retrieval_but_preserve_exact_recovery(self) -> None:
        old = self.remember("Use legacy cache policy", kind="procedural")
        new = self.remember(
            "Use cache policy v2",
            kind="procedural",
            supersedes=(old["memory_id"],),
        )
        self.assertEqual(self.engine.recover(old["memory_id"])["state"], "superseded")
        self.assertTrue(self.engine.recover(old["memory_id"])["exact_recovery"])
        result = self.engine.retrieve("cache policy", scope=self.scope, include_session=False)
        ids = {row["memory_id"] for row in result["results"]}
        self.assertIn(new["memory_id"], ids)
        self.assertNotIn(old["memory_id"], ids)

        forgotten = self.engine.forget(new["memory_id"], reason="superseded by external authority")
        self.assertEqual(forgotten["state"], "forgotten")
        self.assertTrue(forgotten["exact_recovery"])
        result = self.engine.retrieve("cache policy", scope=self.scope, include_session=False)
        self.assertNotIn(new["memory_id"], {row["memory_id"] for row in result["results"]})

    def test_consolidation_preserves_parent_lineage(self) -> None:
        first = self.remember("First implementation observation", kind="episodic")
        second = self.remember("Second implementation observation", kind="episodic")
        consolidated = self.engine.consolidate(
            (first["memory_id"], second["memory_id"]),
            "Implementation observations consolidated",
            kind="semantic",
            scope=self.scope,
            provenance_refs=("evidence:consolidation",),
        )
        self.assertEqual(set(consolidated["consolidated_from"]), {first["memory_id"], second["memory_id"]})
        self.assertEqual(set(consolidated["supersedes"]), {first["memory_id"], second["memory_id"]})
        self.assertTrue(
            {f"memory:{first['memory_id']}", f"memory:{second['memory_id']}"} <= set(consolidated["provenance_refs"])
        )
        self.assertEqual(self.engine.recover(first["memory_id"])["state"], "superseded")
        self.assertEqual(self.engine.recover(second["memory_id"])["state"], "superseded")

    def test_session_retrieval_and_handoff_preserve_exact_recovery(self) -> None:
        item = self.remember("Repository migration uses exact checkpoints", kind="project")
        self.sessions.append(
            "session-a",
            "decision",
            {"decision": "preserve exact checkpoints", "importance": 1.0},
        )
        self.sessions.compact("session-a", views=("decision",))
        result = self.engine.retrieve("exact checkpoints", scope=self.scope)
        self.assertTrue(result["exact_recovery"])
        self.assertIsNotNone(result["session"])
        self.assertTrue(result["session"]["exact_recovery"])
        self.assertTrue(result["receipt_hash"])

        handoff = self.engine.handoff((item["memory_id"],), scope=self.scope)
        self.assertTrue(handoff["exact_recovery"])
        self.assertEqual(handoff["recovery_handles"], [f"memory:{item['memory_id']}"])
        self.assertTrue(handoff["receipt_hash"])

    def test_status_declares_reused_authorities_and_no_parallel_database(self) -> None:
        status = self.engine.status()
        self.assertEqual(status["claim"], "MEMORY_RETRIEVAL_V1")
        self.assertTrue(status["memory_intelligence_store_reused"])
        self.assertTrue(status["session_memory_authority_reused"])
        self.assertFalse(status["new_persistent_database"])
        self.assertTrue(status["same_sqlite_relation_table"])
        self.assertTrue(status["exact_recovery"])


if __name__ == "__main__":
    unittest.main()
