from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from syntavra_runtime.agent_runtime import AgentRuntime
from syntavra_runtime.autonomous_agent import AgentMode
from syntavra_runtime.execution_sandbox import NativeSandboxBroker
from syntavra_runtime.project_model import VerifierSpec
from syntavra_runtime.session_memory import SessionMemory
from syntavra_runtime import verified_workflow_extension


GOOD_PATCH = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)
BAD_PATCH = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+bad\n"
)


class Graph:
    def stats(self):
        return {"files": 1}

    def index_repository(self, project):
        return {"files": 1}

    def query(self, query: str, *, limit: int = 20):
        return [
            {
                "node_id": "n1",
                "name": "value",
                "path": "value.txt",
                "kind": "file",
                "language": "text",
                "start_line": 1,
                "end_line": 1,
                "score": 1.0,
            }
        ][:limit]


class ProjectModel:
    def __init__(self, project: Path):
        self.project = project

    def describe(self):
        return {"languages": ["text"], "project_files": []}

    def discover_verifiers(self):
        return (
            VerifierSpec(
                "value-check",
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('value.txt').read_text(encoding='utf-8') == 'new\\n'",
                ),
                "test",
                1.0,
                "fixture verifier",
                "test",
            ),
        )


class Gateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.config = SimpleNamespace(model="test-model", token_envelope=None)

    def prepare_input_tokens(self, tokens: int) -> None:
        self.prepared_tokens = int(tokens)

    def complete(self, messages, *, system=""):
        self.calls.append({"messages": messages, "system": system})
        if not self.responses:
            raise AssertionError("unexpected provider call")
        text = self.responses.pop(0)
        return SimpleNamespace(
            text=text,
            provider="fake",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 2},
            raw={},
            response_id=f"fake-{len(self.calls)}",
            finish_reason="stop",
        )


def _search(instruction: str) -> str:
    return json.dumps(
        {
            "action": "search",
            "query": instruction,
            "limit": 1,
            "fields": ["name", "path"],
        },
        sort_keys=True,
    )


def _patch(patch: str) -> str:
    return json.dumps(
        {"action": "patch", "patch": patch, "rationale": "fixture"},
        sort_keys=True,
    )


class VerifiedWorkflowGatewayIntegrationTests(unittest.TestCase):
    def test_package_bootstrap_installs_gateway_extension(self) -> None:
        self.assertTrue(
            getattr(AgentRuntime, "_syntavra_verified_workflow_gateway_v1", False)
        )
        self.assertEqual(len(verified_workflow_extension.workflow_runtime_hash()), 64)

    @staticmethod
    def _project(root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "value.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "Syntavra Test"], cwd=project, check=True)
        subprocess.run(["git", "add", "value.txt"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=project, check=True)
        return project

    @staticmethod
    def _event_types(memory: SessionMemory) -> list[str]:
        with sqlite3.connect(memory.path) as db:
            return [
                str(row[0])
                for row in db.execute("SELECT event_type FROM events ORDER BY rowid")
            ]

    def _runtime(self, root: Path) -> tuple[AgentRuntime, SessionMemory]:
        project = self._project(root)
        state_root = root / "state"
        state_root.mkdir()
        memory = SessionMemory(state_root / "memory.sqlite3", project_id="fixture-project")
        runtime = AgentRuntime(
            project=project,
            state_root=state_root / "agent-product",
            graph=Graph(),
            memory=memory,
            sandbox=NativeSandboxBroker(state_root / "sandbox"),
        )
        runtime.project_model = ProjectModel(project)
        return runtime, memory

    @staticmethod
    def _run(runtime: AgentRuntime, instruction: str, gateway: Gateway):
        return runtime.run(
            instruction,
            gateway,
            mode=AgentMode.SAFE_AUTONOMOUS,
            max_attempts=1,
            timeout_seconds=30.0,
        )

    def test_repeated_verified_tool_graph_promotes_and_replays_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime, memory = self._runtime(Path(temp))

            first_instruction = "alpha exact workflow lookup"
            first_gateway = Gateway([_search(first_instruction), _patch(GOOD_PATCH)])
            first = self._run(runtime, first_instruction, first_gateway)
            self.assertTrue(first.ok)
            self.assertEqual(len(first_gateway.calls), 2)
            self.assertNotIn("verified-workflow-promotion", self._event_types(memory))

            second_instruction = "beta exact workflow lookup"
            second_gateway = Gateway([_search(second_instruction), _patch(GOOD_PATCH)])
            second = self._run(runtime, second_instruction, second_gateway)
            self.assertTrue(second.ok)
            self.assertEqual(len(second_gateway.calls), 2)
            self.assertIn("verified-workflow-promotion", self._event_types(memory))

            third_instruction = "gamma exact workflow lookup"
            third_gateway = Gateway([_patch(GOOD_PATCH)])
            third = self._run(runtime, third_instruction, third_gateway)
            self.assertTrue(third.ok)
            self.assertEqual(len(third_gateway.calls), 1)
            replay = [
                row
                for row in third.tool_trace
                if row.get("action") == "verified_workflow_replay"
            ]
            self.assertEqual(len(replay), 1, third.tool_trace)
            self.assertEqual(replay[0]["provider_calls_avoided"], 1)
            self.assertEqual(replay[0]["executed_actions"], 1)
            self.assertIn("verified-workflow-execution", self._event_types(memory))

    def test_failed_fresh_verifier_quarantines_until_new_verified_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime, memory = self._runtime(Path(temp))
            for instruction in ("alpha quarantine workflow", "beta quarantine workflow"):
                gateway = Gateway([_search(instruction), _patch(GOOD_PATCH)])
                result = self._run(runtime, instruction, gateway)
                self.assertTrue(result.ok)
                self.assertEqual(len(gateway.calls), 2)

            failed_instruction = "gamma quarantine workflow"
            failed_gateway = Gateway([_patch(BAD_PATCH)])
            failed = self._run(runtime, failed_instruction, failed_gateway)
            self.assertFalse(failed.ok)
            self.assertEqual(len(failed_gateway.calls), 1)
            self.assertIn("verified-workflow-quarantine", self._event_types(memory))

            provider_instruction = "delta quarantine workflow"
            provider_gateway = Gateway([_search(provider_instruction), _patch(GOOD_PATCH)])
            provider = self._run(runtime, provider_instruction, provider_gateway)
            self.assertTrue(provider.ok)
            self.assertEqual(len(provider_gateway.calls), 2)

            replay_instruction = "epsilon quarantine workflow"
            replay_gateway = Gateway([_patch(GOOD_PATCH)])
            replay = self._run(runtime, replay_instruction, replay_gateway)
            self.assertTrue(replay.ok)
            self.assertEqual(len(replay_gateway.calls), 1)

    def test_runtime_hash_drift_invalidates_old_macro_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime, _ = self._runtime(Path(temp))
            for instruction in ("alpha runtime hash", "beta runtime hash"):
                gateway = Gateway([_search(instruction), _patch(GOOD_PATCH)])
                self.assertTrue(self._run(runtime, instruction, gateway).ok)

            drift_instruction = "gamma runtime hash"
            drift_gateway = Gateway([_search(drift_instruction), _patch(GOOD_PATCH)])
            with mock.patch(
                "syntavra_runtime.verified_workflow_extension.workflow_runtime_hash",
                return_value="f" * 64,
            ):
                drift = self._run(runtime, drift_instruction, drift_gateway)
            self.assertTrue(drift.ok)
            self.assertEqual(len(drift_gateway.calls), 2)
            self.assertFalse(
                any(
                    row.get("action") == "verified_workflow_replay"
                    for row in drift.tool_trace
                )
            )


if __name__ == "__main__":
    unittest.main()
