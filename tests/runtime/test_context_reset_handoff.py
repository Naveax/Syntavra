from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.context_reset_handoff import (
    ContextResetHandoff,
    RepositoryHandoffState,
    SecurityHandoffState,
)
from syntavra_runtime.session_memory import SessionMemory


class ContextResetHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = SessionMemory(Path(self.tmp.name) / "session.db", project_id="syntavra-test")
        opened = self.memory.open("source-session")
        self.assertFalse(opened["restored"])
        self.memory.append("source-session", "task", {"goal": "finish handoff", "importance": 1.0})
        self.handoff = ContextResetHandoff()
        self.repo = RepositoryHandoffState(
            repository="Naveax/Syntavra",
            branch="agent/context-reset-handoff-v1",
            head_sha="1" * 40,
            clean=True,
            upstream_ref="origin/agent/context-reset-handoff-v1",
        )

    def test_continue_preserves_same_session_and_checkpoint(self) -> None:
        result = self.handoff.prepare(
            self.memory,
            "source-session",
            "CONTINUE",
            repository=self.repo,
            evidence_refs=("evidence:one",),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["target_session_id"], "source-session")
        self.assertTrue(result["verified"]["session_chain"])
        self.assertTrue(any(value.startswith("session-checkpoint:") for value in result["recovery_handles"]))

    def test_compact_preserves_exact_history(self) -> None:
        result = self.handoff.prepare(
            self.memory,
            "source-session",
            "COMPACT",
            repository=self.repo,
        )
        self.assertEqual(result["decision"], "COMPACT")
        self.assertTrue(result["compaction_summary_ids"])
        self.assertTrue(self.memory.verify("source-session")["ok"])
        self.assertTrue(result["verified"]["exact_history_preserved"])

    def test_reset_creates_parent_linked_deterministic_target(self) -> None:
        first = self.handoff.prepare(
            self.memory,
            "source-session",
            "RESET",
            repository=self.repo,
            evidence_refs=("evidence:critical",),
            security=SecurityHandoffState(unresolved_critical_evidence=1),
            reason_codes=("CONTEXT_PRESSURE_RECOVERABLE",),
        )
        second = self.handoff.prepare(
            self.memory,
            "source-session",
            "RESET",
            repository=self.repo,
            evidence_refs=("evidence:critical",),
            security=SecurityHandoffState(unresolved_critical_evidence=1),
            reason_codes=("CONTEXT_PRESSURE_RECOVERABLE",),
        )
        self.assertEqual(first["target_session_id"], second["target_session_id"])
        self.assertEqual(first["receipt"]["receipt_hash"], second["receipt"]["receipt_hash"])
        child = self.memory.open(first["target_session_id"])
        self.assertTrue(child["restored"])
        self.assertEqual(child["parents"], ["source-session"])

    def test_branch_creates_parent_linked_session(self) -> None:
        result = self.handoff.prepare(
            self.memory,
            "source-session",
            "BRANCH",
            repository=self.repo,
            reason_codes=("TASK_DRIFT",),
        )
        self.assertNotEqual(result["target_session_id"], "source-session")
        child = self.memory.open(result["target_session_id"])
        self.assertEqual(child["parents"], ["source-session"])

    def test_dirty_repository_requires_recovery_reference(self) -> None:
        with self.assertRaises(ValueError):
            RepositoryHandoffState(
                repository="Naveax/Syntavra",
                branch="work",
                head_sha="2" * 40,
                clean=False,
            )
        state = RepositoryHandoffState(
            repository="Naveax/Syntavra",
            branch="work",
            head_sha="2" * 40,
            clean=False,
            worktree_recovery_ref="artifact:patch-123",
        )
        result = self.handoff.prepare(self.memory, "source-session", "CONTINUE", repository=state)
        self.assertTrue(result["verified"]["repository_recoverable"])

    def test_secret_material_is_never_admissible(self) -> None:
        with self.assertRaises(ValueError):
            SecurityHandoffState(secret_material_present=True)

    def test_tainted_handoff_requires_policy_reference(self) -> None:
        with self.assertRaises(ValueError):
            SecurityHandoffState(tainted=True)
        state = SecurityHandoffState(tainted=True, policy_refs=("policy:taint-1",))
        result = self.handoff.prepare(
            self.memory,
            "source-session",
            "CONTINUE",
            repository=self.repo,
            security=state,
        )
        self.assertTrue(result["security"]["tainted"])
        self.assertEqual(result["security"]["policy_refs"], ("policy:taint-1",))

    def test_reset_with_unresolved_critical_evidence_requires_reference(self) -> None:
        with self.assertRaises(RuntimeError):
            self.handoff.prepare(
                self.memory,
                "source-session",
                "RESET",
                repository=self.repo,
                security=SecurityHandoffState(unresolved_critical_evidence=1),
            )

    def test_receipt_is_order_independent_for_reference_sets(self) -> None:
        first = self.handoff.prepare(
            self.memory,
            "source-session",
            "CONTINUE",
            repository=self.repo,
            evidence_refs=("evidence:b", "evidence:a"),
            recovery_handles=("recovery:b", "recovery:a"),
            reason_codes=("B", "A"),
        )
        second = self.handoff.prepare(
            self.memory,
            "source-session",
            "CONTINUE",
            repository=self.repo,
            evidence_refs=("evidence:a", "evidence:b"),
            recovery_handles=("recovery:a", "recovery:b"),
            reason_codes=("A", "B"),
        )
        self.assertEqual(first["receipt"]["receipt_hash"], second["receipt"]["receipt_hash"])

    def test_status_declares_no_parallel_store_or_secret_transport(self) -> None:
        status = self.handoff.status()
        self.assertEqual(set(status["decisions"]), {"CONTINUE", "COMPACT", "RESET", "BRANCH"})
        self.assertTrue(status["session_memory_authority_reused"])
        self.assertFalse(status["new_persistent_store"])
        self.assertFalse(status["secret_material_allowed"])


if __name__ == "__main__":
    unittest.main()
