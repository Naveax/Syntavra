from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.agent_context_runtime import (
    ConstantContextState,
    ContextBudgetExceeded,
    ProviderTokenCounter,
)
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


class PreModelIngestionFoldGateTests(unittest.TestCase):
    def _externalizer(self, root: Path, project_id: str = "te-p0-01") -> ToolOutputExternalizer:
        evidence = EvidenceStore(root / f"evidence-{project_id}", project_id=project_id)
        return ToolOutputExternalizer(
            root / f"externalizer-{project_id}.sqlite3",
            evidence=evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )

    @staticmethod
    def _large_output(marker: str = "FATAL deterministic verifier failure at src/auth.py:91") -> str:
        body = "".join(f"INFO worker={index % 7} request=normal value={index}\n" for index in range(2200))
        return body + marker + "\n"

    def test_exact_raw_is_persisted_before_first_provider_visibility(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-01-exact-") as directory:
            root = Path(directory)
            externalizer = self._externalizer(root)
            state = ConstantContextState(
                externalizer=externalizer,
                scope_key="task:1",
                preview_limit_bytes=1024,
            )
            raw = self._large_output("CRITICAL exact recovery marker at src/core.py:77")
            receipt = state.ingest(
                key="shell:build",
                tool="shell",
                raw=raw,
                round_number=1,
                command="pytest -q",
            )

            self.assertTrue(receipt.exact_handle)
            self.assertTrue(receipt.artifact_id)
            self.assertLess(receipt.visible_bytes, receipt.original_bytes)
            self.assertEqual(externalizer.restore(receipt.artifact_id), raw.encode("utf-8"))
            self.assertTrue(externalizer.verify(receipt.artifact_id)["ok"])
            visibility = receipt.metadata["provider_visibility"]
            self.assertTrue(visibility["first_visibility_folded"])
            self.assertTrue(visibility["exact_recovery"])
            self.assertEqual(visibility["raw_bytes"], len(raw.encode("utf-8")))
            self.assertEqual(visibility["visible_bytes"], receipt.visible_bytes)
            self.assertGreater(visibility["avoided_bytes"], 0)

    def test_failure_evidence_gets_a_larger_but_still_bounded_first_visibility_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-01-failure-") as directory:
            root = Path(directory)
            externalizer = self._externalizer(root)
            raw = self._large_output()
            success_state = ConstantContextState(
                externalizer=externalizer,
                scope_key="success",
                preview_limit_bytes=1024,
                failure_preview_limit_bytes=4096,
            )
            failure_state = ConstantContextState(
                externalizer=externalizer,
                scope_key="failure",
                preview_limit_bytes=1024,
                failure_preview_limit_bytes=4096,
            )

            success = success_state.ingest(
                key="test.run:pytest",
                tool="test.run",
                raw=raw,
                round_number=1,
                metadata={"ok": True},
            )
            failure = failure_state.ingest(
                key="test.run:pytest",
                tool="test.run",
                raw=raw,
                round_number=1,
                metadata={"ok": False, "mandatory_failure_evidence": True},
            )

            self.assertLessEqual(success.visible_bytes, 1024)
            self.assertLessEqual(failure.visible_bytes, 4096)
            self.assertGreater(failure.visible_bytes, success.visible_bytes)
            self.assertIn("mandatory_failure_evidence", failure.metadata)
            self.assertTrue(failure.metadata["provider_visibility"]["first_visibility_folded"])
            self.assertEqual(externalizer.restore(failure.artifact_id), raw.encode("utf-8"))

    def test_exact_recall_is_scope_bound_and_hard_capped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-01-recall-") as directory:
            root = Path(directory)
            externalizer = self._externalizer(root)
            state = ConstantContextState(
                externalizer=externalizer,
                scope_key="task:recall",
                preview_limit_bytes=1024,
                recall_budget_bytes=2048,
                warm_recall_step_bytes=256,
                warm_recall_ceiling_bytes=2048,
            )
            raw = self._large_output("ERROR recall needle at src/retry.py:44")
            first = state.ingest(
                key="shell:trace",
                tool="shell",
                raw=raw,
                round_number=1,
            )

            recalled = state.recall(
                first.artifact_id,
                lens="failures",
                budget_bytes=100_000,
                round_number=2,
            )
            self.assertEqual(recalled.status, "BOUNDED_RECALL")
            self.assertLessEqual(recalled.visible_bytes, 2048)
            self.assertEqual(recalled.metadata["admitted_budget_bytes"], 2048)
            self.assertEqual(recalled.metadata["requested_budget_bytes"], 100_000)
            self.assertEqual(recalled.metadata["recall_count"], 1)
            self.assertIn("UNTRUSTED TOOL EVIDENCE RECALL", recalled.preview)
            self.assertEqual(state.recall_counts["shell:trace"], 1)
            self.assertEqual(externalizer.restore(recalled.artifact_id), raw.encode("utf-8"))

            with self.assertRaises(PermissionError):
                state.recall("ext-not-admitted-in-this-scope", lens="salient")

            changed = raw.replace("request=normal", "request=changed", 1)
            warmed = state.ingest(
                key="shell:trace",
                tool="shell",
                raw=changed,
                round_number=3,
            )
            self.assertEqual(warmed.metadata["provider_visibility"]["warm_recall_count"], 1)
            self.assertLessEqual(warmed.visible_bytes, 1280)

    def test_mandatory_failure_preview_is_never_dropped_to_fit_budget(self) -> None:
        state = ConstantContextState(
            preview_limit_bytes=256,
            failure_preview_limit_bytes=2048,
        )
        state.ingest(
            key="test.run:failure",
            tool="test.run",
            raw="ERROR verifier failed\n" + ("trace line\n" * 120),
            round_number=1,
            metadata={"mandatory_failure_evidence": True},
        )
        with self.assertRaises(ContextBudgetExceeded):
            state.compile(
                {"instruction": "fix the failing verifier"},
                counter=ProviderTokenCounter("sequence"),
                token_budget=256,
                byte_budget=512,
            )

    def test_unchanged_results_are_zero_preview_receipts_with_attribution(self) -> None:
        state = ConstantContextState(preview_limit_bytes=512)
        first = state.ingest(key="poll:status", tool="status", raw="unchanged", round_number=1)
        second = state.ingest(key="poll:status", tool="status", raw="unchanged", round_number=2)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(second.status, "UNCHANGED")
        self.assertEqual(second.visible_bytes, 0)
        visibility = second.metadata["provider_visibility"]
        self.assertEqual(visibility["visible_bytes"], 0)
        self.assertEqual(visibility["avoided_bytes"], len(b"unchanged"))
        self.assertTrue(any(row.get("status") == "UNCHANGED" for row in state.causal))


if __name__ == "__main__":
    unittest.main()
