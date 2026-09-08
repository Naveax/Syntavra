from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from syntavra_runtime.agent_runtime import GatewayPatchProvider
from syntavra_runtime.autonomous_agent import AgentMode, AgentTask
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


PATCH = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)
SECRET = "RAW_CANDIDATE_MUST_STAY_LOCAL"


class Graph:
    def query(self, query: str, *, limit: int = 20):
        rows = [
            {
                "node_id": "n1",
                "name": "alpha",
                "path": "src/a.py",
                "kind": "function",
                "language": "python",
                "start_line": 1,
                "end_line": 4,
                "score": 0.9,
                "metadata_json": SECRET,
            },
            {
                "node_id": "n2",
                "name": "beta",
                "path": "src/b.py",
                "kind": "function",
                "language": "python",
                "start_line": 10,
                "end_line": 14,
                "score": 0.7,
                "metadata_json": SECRET,
            },
        ]
        return rows[:limit]


class ProjectModel:
    def describe(self):
        return {"languages": ["python"]}

    def discover_verifiers(self):
        return ()


class ContextAssembler:
    def assemble(self, instruction, semantic_results, *, root=None):
        return {"instruction": instruction, "semantic_results": []}


class Gateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.config = SimpleNamespace(model="test-model", token_envelope=None)

    def prepare_input_tokens(self, tokens: int) -> None:
        self.prepared_tokens = tokens

    def complete(self, messages, *, system=""):
        self.calls.append({"messages": messages, "system": system})
        text = self.responses.pop(0)
        return SimpleNamespace(
            text=text,
            provider="fake",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 2},
            raw={},
            response_id="",
            finish_reason="stop",
        )


class QueryPushdownGatewayTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "value.txt").write_text("old\n", encoding="utf-8")
        return project

    @staticmethod
    def _task() -> AgentTask:
        return AgentTask(
            instruction="count matching functions and then patch value",
            verifier=("python", "-c", "assert True"),
            mode=AgentMode.SAFE_AUTONOMOUS,
            max_attempts=1,
        )

    def test_search_reduce_captures_exact_raw_window_but_only_exposes_compact_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            evidence = EvidenceStore(root / "evidence", project_id="test-project")
            externalizer = ToolOutputExternalizer(
                root / "externalizer.sqlite3",
                evidence=evidence,
                policy=ExternalizationPolicy.for_profile("compact"),
            )
            gateway = Gateway(
                [
                    '{"action":"search_reduce","query":"function","operator":"count","fields":["kind"],"filters":{"kind":"function"}}',
                    '{"action":"patch","patch":' + json.dumps(PATCH) + ',"rationale":"done"}',
                ]
            )
            provider = GatewayPatchProvider(
                gateway,
                project=project,
                graph=Graph(),
                project_model=ProjectModel(),
                context_assembler=ContextAssembler(),
                externalizer=externalizer,
                max_tool_rounds=3,
                provider_token_budget=6000,
                provider_byte_budget=24000,
            )
            proposal = provider.propose(
                self._task(),
                {"workspace": str(project), "attempt": 1, "semantic_results": [], "current_diff": "", "changed_files": ()},
                None,
            )
            self.assertEqual(proposal.patch, PATCH)
            self.assertEqual(len(gateway.calls), 2)
            reduction_trace = provider.trace[0]
            self.assertEqual(reduction_trace["action"], "search_reduce")
            self.assertTrue(reduction_trace["exact_before_lossy"])
            self.assertTrue(reduction_trace["source_window_complete"])
            self.assertTrue(evidence.verify(reduction_trace["source_exact"]))
            exact_raw = evidence.get(reduction_trace["source_exact"]).decode("utf-8")
            self.assertIn(SECRET, exact_raw)

            second_packet = "\n".join(
                str(message.get("content") or "") for message in gateway.calls[1]["messages"]
            )
            self.assertNotIn(SECRET, second_packet)
            compiled = json.loads(second_packet)
            reduction_items = [
                item
                for item in compiled.get("active_evidence", [])
                if str(item.get("key") or "").startswith("repo.search_reduce:")
            ]
            self.assertEqual(len(reduction_items), 1, compiled)
            reduction_evidence = json.loads(str(reduction_items[0]["evidence"]))
            self.assertEqual(reduction_evidence["value"]["count"], 2)
            self.assertEqual(reduction_evidence["source_exact"], reduction_trace["source_exact"])
            self.assertTrue(evidence.verify(str(reduction_items[0]["exact"])))
            self.assertIn(reduction_trace["source_exact"], second_packet)

    def test_search_reduce_fails_closed_without_exact_externalizer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            gateway = Gateway([
                '{"action":"search_reduce","query":"function","operator":"count","fields":["kind"]}'
            ])
            provider = GatewayPatchProvider(
                gateway,
                project=project,
                graph=Graph(),
                project_model=ProjectModel(),
                context_assembler=ContextAssembler(),
                externalizer=None,
                max_tool_rounds=1,
            )
            with self.assertRaisesRegex(RuntimeError, "exact externalizer"):
                provider.propose(
                    self._task(),
                    {"workspace": str(project), "attempt": 1, "semantic_results": [], "current_diff": "", "changed_files": ()},
                    None,
                )


if __name__ == "__main__":
    unittest.main()
