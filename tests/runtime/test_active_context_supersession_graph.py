from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.agent_context_runtime import ConstantContextState, ProviderTokenCounter
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


class ActiveContextSupersessionGraphTests(unittest.TestCase):
    def _externalizer(self, root: Path, project_id: str) -> ToolOutputExternalizer:
        evidence = EvidenceStore(root / f"evidence-{project_id}", project_id=project_id)
        return ToolOutputExternalizer(
            root / f"externalizer-{project_id}.sqlite3",
            evidence=evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )

    @staticmethod
    def _body(label: str) -> str:
        return "".join(f"{label} line={index} value={index % 19}\n" for index in range(2400))

    def test_same_repo_read_view_supersedes_body_to_exact_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-read-") as directory:
            root = Path(directory)
            externalizer = self._externalizer(root, "same-read")
            state = ConstantContextState(externalizer=externalizer, scope_key="task:same-read")
            old_raw = self._body("old")
            new_raw = self._body("new")
            old = state.ingest(
                key="read:caller-a",
                tool="repo.read",
                raw=old_raw,
                round_number=1,
                path="src/auth.py",
                metadata={"start_line": 1, "end_line": 220},
            )
            new = state.ingest(
                key="read:caller-b",
                tool="repo.read",
                raw=new_raw,
                round_number=2,
                path="src/auth.py",
                metadata={"start_line": 1, "end_line": 220},
            )

            self.assertEqual(len(state.active), 1)
            self.assertEqual(state.active[0].content_hash, new.content_hash)
            superseded = [row for row in state.causal if row.get("status") == "SUPERSEDED_TO_HANDLE"]
            self.assertEqual(len(superseded), 1)
            self.assertEqual(superseded[0]["sha256"], old.content_hash)
            self.assertEqual(superseded[0]["superseded_by_sha256"], new.content_hash)
            self.assertTrue(superseded[0]["exact"])
            self.assertEqual(externalizer.restore(old.artifact_id), old_raw.encode("utf-8"))

    def test_complete_search_shape_supersedes_across_caller_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-search-") as directory:
            root = Path(directory)
            state = ConstantContextState(
                externalizer=self._externalizer(root, "search-complete"),
                scope_key="task:search-complete",
            )
            shape = {
                "query": "ProviderTokenEnvelope",
                "projected_fields": ["path", "symbol", "line"],
                "filters": {"language": "python"},
            }
            first = state.ingest(
                key="search:one",
                tool="repo.search",
                raw=self._body("search-old"),
                round_number=1,
                metadata=shape,
            )
            second = state.ingest(
                key="search:two",
                tool="repo.search",
                raw=self._body("search-new"),
                round_number=2,
                metadata=shape,
            )

            self.assertEqual(len(state.active), 1)
            self.assertEqual(state.active[0].content_hash, second.content_hash)
            row = next(row for row in state.causal if row.get("status") == "SUPERSEDED_TO_HANDLE")
            self.assertEqual(row["sha256"], first.content_hash)

    def test_incomplete_search_shape_never_guesses_cross_generation_equivalence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-search-opaque-") as directory:
            root = Path(directory)
            state = ConstantContextState(
                externalizer=self._externalizer(root, "search-opaque"),
                scope_key="task:search-opaque",
            )
            state.ingest(
                key="search:opaque",
                tool="repo.search",
                raw=self._body("opaque-old"),
                round_number=1,
                metadata={"query": "needle"},
            )
            state.ingest(
                key="search:opaque",
                tool="repo.search",
                raw=self._body("opaque-new"),
                round_number=2,
                metadata={"query": "needle"},
            )

            self.assertEqual(len(state.active), 2)
            self.assertFalse(any(row.get("status") == "SUPERSEDED_TO_HANDLE" for row in state.causal))

    def test_invalidation_fingerprint_is_attributed_without_splitting_logical_stream(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-invalidation-") as directory:
            root = Path(directory)
            state = ConstantContextState(
                externalizer=self._externalizer(root, "invalidation"),
                scope_key="task:invalidation",
            )
            state.ingest(
                key="read:a",
                tool="repo.read",
                raw=self._body("generation-a"),
                round_number=1,
                path="src/runtime.py",
                metadata={"start_line": 10, "end_line": 80, "invalidation_fingerprint": "tree-a"},
            )
            state.ingest(
                key="read:b",
                tool="repo.read",
                raw=self._body("generation-b"),
                round_number=2,
                path="src/runtime.py",
                metadata={"start_line": 10, "end_line": 80, "invalidation_fingerprint": "tree-b"},
            )

            row = next(row for row in state.causal if row.get("status") == "SUPERSEDED_TO_HANDLE")
            self.assertTrue(row["invalidation_changed"])
            self.assertEqual(row["previous_invalidation_fingerprint"], "tree-a")
            self.assertEqual(row["current_invalidation_fingerprint"], "tree-b")

    def test_mandatory_evidence_stays_pinned_and_new_canonical_survives_soft_capacity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-pinned-") as directory:
            root = Path(directory)
            state = ConstantContextState(
                externalizer=self._externalizer(root, "mandatory-pinned"),
                scope_key="task:mandatory-pinned",
                max_active=1,
            )
            old = state.ingest(
                key="test:pytest",
                tool="test.run",
                raw=self._body("mandatory-failure"),
                round_number=1,
                metadata={
                    "name": "pytest",
                    "argv": ["pytest", "-q"],
                    "mandatory_failure_evidence": True,
                },
            )
            new = state.ingest(
                key="test:pytest",
                tool="test.run",
                raw=self._body("later-state"),
                round_number=2,
                metadata={"name": "pytest", "argv": ["pytest", "-q"]},
            )

            hashes = {receipt.content_hash for receipt in state.active}
            self.assertIn(old.content_hash, hashes)
            self.assertIn(new.content_hash, hashes)
            self.assertEqual(len(state.active), 2)
            row = next(row for row in state.causal if row.get("status") == "SUPERSEDED")
            self.assertTrue(row["body_retained_active"])
            self.assertTrue(row["body_drop_blocked"])
            self.assertEqual(row["body_drop_reason"], "MANDATORY_EVIDENCE")

    def test_unrecoverable_body_stays_pinned_instead_of_being_deleted(self) -> None:
        state = ConstantContextState(max_active=1, preview_limit_bytes=512)
        old = state.ingest(
            key="read:volatile",
            tool="repo.read",
            raw="old volatile body",
            round_number=1,
            path="src/volatile.py",
            metadata={"start_line": 1, "end_line": 20},
        )
        new = state.ingest(
            key="read:volatile",
            tool="repo.read",
            raw="new volatile body",
            round_number=2,
            path="src/volatile.py",
            metadata={"start_line": 1, "end_line": 20},
        )

        hashes = {receipt.content_hash for receipt in state.active}
        self.assertIn(old.content_hash, hashes)
        self.assertIn(new.content_hash, hashes)
        row = next(row for row in state.causal if row.get("status") == "SUPERSEDED")
        self.assertEqual(row["body_drop_reason"], "NO_EXACT_RECOVERY")

    def test_stale_recall_body_is_removed_when_new_canonical_state_arrives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-02-recall-") as directory:
            root = Path(directory)
            state = ConstantContextState(
                externalizer=self._externalizer(root, "stale-recall"),
                scope_key="task:stale-recall",
                recall_budget_bytes=2048,
            )
            first = state.ingest(
                key="read:source",
                tool="repo.read",
                raw=self._body("recall-old"),
                round_number=1,
                path="src/cache.py",
                metadata={"start_line": 1, "end_line": 120},
            )
            recalled = state.recall(first.artifact_id, lens="head", budget_bytes=1024, round_number=2)
            self.assertTrue(any(item.key == recalled.key for item in state.active))

            state.ingest(
                key="read:source-new-key",
                tool="repo.read",
                raw=self._body("recall-new"),
                round_number=3,
                path="src/cache.py",
                metadata={"start_line": 1, "end_line": 120},
            )

            self.assertFalse(any(bool(item.metadata.get("recall")) for item in state.active))
            row = next(row for row in reversed(state.causal) if row.get("status") == "SUPERSEDED_TO_HANDLE")
            self.assertEqual(row["stale_recall_bodies_removed"], 1)

    def test_compiled_packet_advertises_supersession_policy_without_replaying_history(self) -> None:
        state = ConstantContextState(preview_limit_bytes=256)
        state.ingest(
            key="volatile",
            tool="status",
            raw="small state",
            round_number=1,
        )
        compiled = state.compile(
            {"instruction": "inspect current state"},
            counter=ProviderTokenCounter("sequence"),
            token_budget=4000,
            byte_budget=16000,
        )
        packet = json.loads(compiled.text)
        self.assertTrue(packet["context_policy"]["active_context_supersession_graph"])
        self.assertFalse(packet["context_policy"]["raw_history_replayed"])
        self.assertFalse(packet["context_policy"]["unrecoverable_evidence_truncation"])


if __name__ == "__main__":
    unittest.main()
