from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.agent_context_runtime import ConstantContextState, ProviderTokenCounter
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


class CausalHistorySkeletonTests(unittest.TestCase):
    @staticmethod
    def _externalizer(root: Path, scope: str) -> ToolOutputExternalizer:
        root.mkdir(parents=True, exist_ok=True)
        evidence = EvidenceStore(root / "evidence", project_id=scope)
        return ToolOutputExternalizer(
            root / "externalizer.sqlite3",
            evidence=evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )

    def _run_session(self, root: Path, *, scope: str, rounds: int):
        state = ConstantContextState(
            externalizer=self._externalizer(root, scope),
            scope_key=scope,
            max_active=4,
            max_causal=8,
            preview_limit_bytes=384,
            recall_budget_bytes=1024,
        )
        raw_tail = "payload=" + ("x" * 3072)
        for index in range(1, rounds + 1):
            stream = index % 4
            state.ingest(
                key=f"repo.read:caller-{index}",
                tool="repo.read",
                raw=f"revision={index}\n{raw_tail}",
                round_number=index,
                path=f"src/module_{stream}.py",
                metadata={
                    "start_line": 1,
                    "end_line": 120,
                    "invalidation_fingerprint": f"workspace-{index}",
                },
            )
        compiled = state.compile(
            {"instruction": "preserve exact current evidence and causal lineage"},
            counter=ProviderTokenCounter("sequence"),
            token_budget=6000,
            byte_budget=24_000,
        )
        return state, compiled, json.loads(compiled.text)

    def test_long_session_provider_context_is_bounded_by_active_state_not_round_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-04-") as directory:
            root = Path(directory)
            short_state, short_compiled, _ = self._run_session(
                root / "short",
                scope="causal-short",
                rounds=32,
            )
            long_state, long_compiled, packet = self._run_session(
                root / "long",
                scope="causal-long",
                rounds=160,
            )

            self.assertEqual(len(short_state.active), 4)
            self.assertEqual(len(long_state.active), 4)
            self.assertEqual(len(short_state.causal), 8)
            self.assertEqual(len(long_state.causal), 8)

            self.assertLessEqual(abs(long_compiled.visible_bytes - short_compiled.visible_bytes), 2048)
            self.assertLessEqual(abs(long_compiled.tokens - short_compiled.tokens), 512)
            self.assertLessEqual(long_compiled.visible_bytes, 24_000)
            self.assertLessEqual(long_compiled.tokens, 6000)

            self.assertTrue(all(receipt.exact_handle for receipt in long_state.active))
            self.assertTrue(all(row.get("exact") for row in long_state.causal))
            self.assertTrue(all(row.get("status") == "SUPERSEDED_TO_HANDLE" for row in long_state.causal))

            self.assertFalse(packet["context_policy"]["raw_history_replayed"])
            self.assertEqual(packet["context_policy"]["active_evidence_count"], 4)
            self.assertEqual(packet["context_policy"]["causal_receipt_count"], 8)
            self.assertEqual(
                packet["task"]["instruction"],
                "preserve exact current evidence and causal lineage",
            )

    def test_causal_receipts_are_body_free_exact_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-04-body-free-") as directory:
            state, _, packet = self._run_session(
                Path(directory),
                scope="causal-body-free",
                rounds=48,
            )

            forbidden_body_keys = {"evidence", "content", "preview", "stdout", "stderr", "raw"}
            self.assertTrue(state.causal)
            for row in state.causal:
                self.assertTrue(forbidden_body_keys.isdisjoint(row))
                self.assertEqual(len(str(row["sha256"])), 64)
                self.assertTrue(row.get("exact"))
                self.assertTrue(row.get("superseded_by_sha256"))

            serialized = json.dumps(packet["causal_receipts"], sort_keys=True)
            self.assertNotIn("payload=" + ("x" * 128), serialized)
            self.assertNotIn("revision=48", serialized)


if __name__ == "__main__":
    unittest.main()
