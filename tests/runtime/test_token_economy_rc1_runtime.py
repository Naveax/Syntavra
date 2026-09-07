from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.agent_context_runtime import (
    ConstantContextState,
    ContextBudgetExceeded,
    ProviderTokenCounter,
)
from syntavra_runtime.agent_runtime import GatewayPatchProvider
from syntavra_runtime.autonomous_agent import AgentMode, AgentTask
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.model_gateway import SequenceModelGateway
from syntavra_runtime.project_model import ProjectModel
from syntavra_runtime.provider_call_observation import ProviderCallObservationLedger
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


def _project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    return project


class Graph:
    def query(self, query: str, *, limit: int = 20):
        return [
            {
                "node_id": "n1",
                "name": "VALUE",
                "path": "module.py",
                "start_line": 1,
                "end_line": 1,
                "query": query,
                "limit": limit,
            }
        ]

    def impact(self, node_id: str, *, max_depth: int = 6):
        return {"root": node_id, "impacted": [], "max_depth": max_depth}


class RecordingGateway(SequenceModelGateway):
    def __init__(self, responses):
        super().__init__(responses, model="sequence")
        self.payloads: list[str] = []

    def complete(self, messages, *, system: str = ""):
        self.payloads.append(json.dumps({"system": system, "messages": list(messages)}, ensure_ascii=False, sort_keys=True))
        return super().complete(messages, system=system)


class TokenEconomyRC1RuntimeTests(unittest.TestCase):
    def test_mandatory_instruction_is_never_silently_truncated(self) -> None:
        state = ConstantContextState()
        instruction = "MANDATORY-" + ("x" * 20_000)
        with self.assertRaises(ContextBudgetExceeded):
            state.compile(
                {"instruction": instruction, "optional": "y" * 20_000},
                counter=ProviderTokenCounter("sequence"),
                token_budget=256,
                byte_budget=1024,
            )

    def test_supersession_and_no_change_are_handle_receipts_not_raw_history(self) -> None:
        state = ConstantContextState(max_active=2, max_causal=8, preview_limit_bytes=512)
        first = state.ingest(key="repo.read:a.py:1:10", tool="repo.read", raw="alpha", round_number=1)
        unchanged = state.ingest(key="repo.read:a.py:1:10", tool="repo.read", raw="alpha", round_number=2)
        newer = state.ingest(key="repo.read:a.py:1:10", tool="repo.read", raw="beta", round_number=3)

        self.assertEqual(first.content_hash, unchanged.content_hash)
        self.assertEqual(unchanged.status, "UNCHANGED")
        self.assertEqual(unchanged.preview, "")
        self.assertNotEqual(newer.content_hash, first.content_hash)
        self.assertTrue(any(row.get("status") == "UNCHANGED" for row in state.causal))
        self.assertTrue(any(row.get("status") == "SUPERSEDED" for row in state.causal))
        self.assertEqual(state.active[-1].preview, "beta")

    def test_fifty_round_tool_loop_has_bounded_constant_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-rc1-50-round-") as directory:
            project = _project(Path(directory))
            model = ProjectModel(project)
            responses = [
                {"action": "search", "query": "VALUE"}
                for _ in range(49)
            ] + [
                {
                    "action": "edit",
                    "rationale": "update value",
                    "edits": [
                        {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1}
                    ],
                }
            ]
            gateway = RecordingGateway(responses)
            provider = GatewayPatchProvider(
                gateway,
                project=project,
                graph=Graph(),
                project_model=model,
                max_tool_rounds=64,
                provider_token_budget=6000,
                provider_byte_budget=24_000,
            )
            proposal = provider.propose(
                AgentTask("change VALUE", model.primary_verifier().argv, mode=AgentMode.SAFE_AUTONOMOUS),
                {
                    "workspace": str(project),
                    "attempt": 1,
                    "semantic_results": Graph().query("VALUE"),
                    "current_diff": "",
                    "changed_files": [],
                },
                None,
            )

            self.assertIn("+VALUE = 2", proposal.patch)
            self.assertEqual(len(gateway.payloads), 50)
            self.assertEqual(len(gateway.prepared_history), 50)
            self.assertLessEqual(max(gateway.prepared_history), 6000)
            sizes = [len(value.encode("utf-8")) for value in gateway.payloads]
            self.assertLessEqual(max(sizes), 24_000)
            self.assertLess(max(sizes[-20:]) - min(sizes[-20:]), 5000)
            self.assertFalse(proposal.metadata["context"]["raw_history_replayed"])
            self.assertLessEqual(proposal.metadata["context"]["max_visible_bytes"], 24_000)
            self.assertIsNotNone(provider._state)
            assert provider._state is not None
            self.assertLessEqual(len(provider._state.active), 8)
            self.assertLessEqual(len(provider._state.causal), 16)
            self.assertTrue(any(row.get("status") == "UNCHANGED" for row in provider._state.causal))
            self.assertTrue(all(len(json.loads(value)["messages"]) == 1 for value in gateway.payloads))

    def test_externalized_inspect_is_exactly_recoverable_and_smaller(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-rc1-externalize-") as directory:
            root = Path(directory)
            project = _project(root)
            marker = "CRITICAL_MARKER_FOR_EXACT_RECOVERY"
            large = "".join(f"line-{index:05d} value value value\n" for index in range(4000)) + marker + "\n"
            (project / "module.py").write_text(large, encoding="utf-8")
            model = ProjectModel(project)
            evidence = EvidenceStore(root / "evidence", project_id="rc1")
            externalizer = ToolOutputExternalizer(
                root / "externalizer.sqlite3",
                evidence=evidence,
                policy=ExternalizationPolicy.for_profile("compact"),
            )
            gateway = RecordingGateway(
                [
                    {"action": "inspect", "path": "module.py", "start_line": 1, "end_line": 4001},
                    {
                        "action": "patch",
                        "rationale": "test-only proposal",
                        "patch": "diff --git a/module.py b/module.py\n--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-line-00000 value value value\n+line-00000 changed\n",
                    },
                ]
            )
            provider = GatewayPatchProvider(
                gateway,
                project=project,
                graph=Graph(),
                project_model=model,
                externalizer=externalizer,
                max_inspect_total_bytes=120_000,
                max_file_bytes=120_000,
            )
            provider.propose(
                AgentTask("inspect module", model.primary_verifier().argv, mode=AgentMode.REVIEW_REQUIRED),
                {"workspace": str(project), "attempt": 1, "semantic_results": Graph().query("module"), "changed_files": []},
                None,
            )
            self.assertIsNotNone(provider._state)
            assert provider._state is not None
            receipts = [row for row in provider._state.active if row.tool == "repo.read"]
            self.assertEqual(len(receipts), 1)
            receipt = receipts[0]
            self.assertTrue(receipt.exact_handle)
            self.assertTrue(receipt.artifact_id)
            self.assertLess(receipt.visible_bytes, receipt.original_bytes)
            restored = externalizer.restore(receipt.artifact_id).decode("utf-8", errors="replace")
            self.assertIn(marker, restored)
            self.assertTrue(externalizer.verify(receipt.artifact_id)["ok"])

    def test_ranged_inspect_does_not_return_unrequested_file_body(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-rc1-range-") as directory:
            project = _project(Path(directory))
            (project / "module.py").write_text(
                "".join(f"L{index:03d}\n" for index in range(1, 301)),
                encoding="utf-8",
            )
            model = ProjectModel(project)
            provider = GatewayPatchProvider(
                SequenceModelGateway([{"action": "patch", "patch": "x", "rationale": "x"}]),
                project=project,
                graph=Graph(),
                project_model=model,
            )
            rows = provider._inspect_action(
                {"action": "inspect", "path": "module.py", "start_line": 100, "end_line": 110},
                root=project,
            )
            self.assertEqual(len(rows), 1)
            self.assertIn("L100", rows[0]["content"])
            self.assertIn("L110", rows[0]["content"])
            self.assertNotIn("L001", rows[0]["content"])
            self.assertEqual(rows[0]["start_line"], 100)

    def test_provider_observation_never_upgrades_estimates_into_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="syntavra-rc1-observation-") as directory:
            ledger = ProviderCallObservationLedger(Path(directory) / "provider.sqlite3")
            ledger.record_call(
                task_id="unproved",
                arm_id="candidate",
                repetition=1,
                round_number=1,
                provider="sequence",
                model="sequence",
                usage={},
                response_id="",
                response={"text": "ok"},
                locally_counted_input_tokens=123,
                counting_method="LOCALLY_ESTIMATED:utf8-bytes-div-4",
                context_hash="a" * 64,
                elapsed_ms=1,
            )
            summary = ledger.summary(task_id="unproved", arm_id="candidate")
            self.assertFalse(summary["provider_proof_complete"])
            self.assertEqual(summary["locally_counted_input_tokens"], 123)

            ledger.record_call(
                task_id="paired",
                arm_id="baseline",
                repetition=1,
                round_number=1,
                provider="openai",
                model="test",
                usage={"input_tokens": 100, "output_tokens": 20},
                response_id="resp-baseline",
                response={"id": "resp-baseline"},
                locally_counted_input_tokens=100,
                counting_method="LOCALLY_TOKENIZED:test",
                context_hash="b" * 64,
                elapsed_ms=10,
            )
            ledger.record_call(
                task_id="paired",
                arm_id="candidate",
                repetition=1,
                round_number=1,
                provider="openai",
                model="test",
                usage={"input_tokens": 50, "output_tokens": 10},
                response_id="resp-candidate",
                response={"id": "resp-candidate"},
                locally_counted_input_tokens=50,
                counting_method="LOCALLY_TOKENIZED:test",
                context_hash="c" * 64,
                elapsed_ms=8,
            )
            comparison = ledger.paired_token_comparison(
                task_id="paired", baseline_arm="baseline", candidate_arm="candidate"
            )
            self.assertEqual(comparison["claim_level"], "PAIRED_PROVIDER_OBSERVED_TOKENS")
            self.assertEqual(comparison["baseline_provider_tokens"], 120)
            self.assertEqual(comparison["candidate_provider_tokens"], 60)
            self.assertEqual(comparison["provider_token_reduction_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
