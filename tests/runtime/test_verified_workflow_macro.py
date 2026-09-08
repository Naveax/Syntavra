from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.session_memory import SessionMemory
from syntavra_runtime.verified_workflow import (
    VerifiedWorkflowCompiler,
    WorkflowCompatibilityIdentity,
    WorkflowMacroAmbiguityError,
    WorkflowMacroError,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compatibility(*, environment: str = "environment-v1") -> WorkflowCompatibilityIdentity:
    return WorkflowCompatibilityIdentity(
        project_id="test-project",
        repository_fingerprint=digest("repo-state"),
        verifier_hash=digest("verifier"),
        policy_hash=digest("policy"),
        task_family_hash=digest("coding-agent"),
        dependency_hash=digest("dependencies"),
        toolchain_hash=digest("toolchain"),
        environment_hash=digest(environment),
        tool_schema_hash=digest("tool-schema"),
        security_hash=digest("security"),
        verifier_contract_hash=digest("verifier-contract"),
    )


SEARCH_GRAPH = (
    {"action": "search", "query": "TASK", "limit": 8, "fields": ["name", "path"]},
    {"action": "inspect", "path": "src/example.py", "start_line": 1, "end_line": 80},
    {"action": "verifiers"},
)
REDUCTION_GRAPH = (
    {
        "action": "search_reduce",
        "query": "TASK",
        "operator": "count",
        "fields": ["kind"],
        "filters": {"kind": "function"},
    },
    {"action": "inspect", "path": "src/example.py", "start_line": 1, "end_line": 80},
)


class VerifiedWorkflowMacroTests(unittest.TestCase):
    def compiler(self, root: Path) -> VerifiedWorkflowCompiler:
        memory = SessionMemory(root / "session-memory.sqlite3", project_id="test-project")
        return VerifiedWorkflowCompiler(memory)

    def record(
        self,
        compiler: VerifiedWorkflowCompiler,
        identity: WorkflowCompatibilityIdentity,
        graph,
        *,
        task: str,
        receipt: str,
    ):
        rendered = []
        for action in graph:
            row = dict(action)
            if row.get("query") == "TASK":
                row["query"] = task
            rendered.append(row)
        return compiler.record_verified_experience(
            identity,
            rendered,
            parameters={"task": task},
            task_reference={"instruction_hash": digest(task), "run_id": digest("run:" + task)},
            verifier_receipt_hash=digest(receipt),
        )

    def test_two_independent_verified_experiences_promote_and_bind_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()

            first = self.record(
                compiler, identity, SEARCH_GRAPH,
                task="find alpha implementation", receipt="verify-alpha",
            )
            self.assertIsNone(first)

            promoted = self.record(
                compiler, identity, SEARCH_GRAPH,
                task="find beta implementation", receipt="verify-beta",
            )
            self.assertIsNotNone(promoted)
            self.assertEqual(len(promoted.experience_ids), 2)

            resolved = compiler.resolve(identity, parameters={"task": "find gamma implementation"})
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.macro.macro_id, promoted.macro_id)
            self.assertEqual(resolved.actions[0]["query"], "find gamma implementation")
            self.assertEqual(resolved.actions[1]["path"], "src/example.py")
            self.assertNotIn("$param", str(resolved.actions))

    def test_duplicate_experience_does_not_satisfy_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()
            first = self.record(
                compiler, identity, SEARCH_GRAPH,
                task="same task", receipt="same verifier receipt",
            )
            second = self.record(
                compiler, identity, SEARCH_GRAPH,
                task="same task", receipt="same verifier receipt",
            )
            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertEqual(compiler.promoted_macros(identity), ())

    def test_mutating_actions_are_never_macro_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()
            with self.assertRaisesRegex(PermissionError, "mutating workflow action"):
                compiler.record_verified_experience(
                    identity,
                    [{"action": "edit", "edits": []}],
                    parameters={"task": "anything"},
                    task_reference={"instruction_hash": digest("anything")},
                    verifier_receipt_hash=digest("verifier"),
                )
            with self.assertRaisesRegex(PermissionError, "mutating workflow action"):
                compiler.record_verified_experience(
                    identity,
                    [{"action": "patch", "patch": "diff"}],
                    parameters={"task": "anything"},
                    task_reference={"instruction_hash": digest("anything")},
                    verifier_receipt_hash=digest("verifier"),
                )

    def test_incompatible_environment_never_resolves_existing_macro(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()
            self.record(compiler, identity, SEARCH_GRAPH, task="alpha task", receipt="verify-a")
            self.record(compiler, identity, SEARCH_GRAPH, task="beta task", receipt="verify-b")
            drifted = compatibility(environment="environment-v2")
            self.assertIsNone(compiler.resolve(drifted, parameters={"task": "gamma task"}))

    def test_multiple_compatible_macros_fail_closed_without_explicit_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()
            self.record(compiler, identity, SEARCH_GRAPH, task="alpha search", receipt="search-a")
            first = self.record(
                compiler, identity, SEARCH_GRAPH, task="beta search", receipt="search-b"
            )
            self.record(
                compiler, identity, REDUCTION_GRAPH, task="alpha reduction", receipt="reduce-a"
            )
            second = self.record(
                compiler, identity, REDUCTION_GRAPH, task="beta reduction", receipt="reduce-b"
            )
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first.macro_id, second.macro_id)

            with self.assertRaises(WorkflowMacroAmbiguityError):
                compiler.resolve(identity, parameters={"task": "new task"})

            selected = compiler.resolve(
                identity,
                parameters={"task": "new task"},
                macro_id=first.macro_id,
            )
            self.assertEqual(selected.macro.macro_id, first.macro_id)

    def test_successful_execution_requires_fresh_reverification_and_records_avoided_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            identity = compatibility()
            self.record(compiler, identity, SEARCH_GRAPH, task="alpha task", receipt="verify-a")
            macro = self.record(
                compiler, identity, SEARCH_GRAPH, task="beta task", receipt="verify-b"
            )
            bound = compiler.resolve(
                identity,
                parameters={"task": "gamma task"},
                macro_id=macro.macro_id,
            )
            with self.assertRaisesRegex(WorkflowMacroError, "fresh re-verification"):
                compiler.record_execution(
                    identity,
                    bound,
                    ok=True,
                    reverified=False,
                    provider_calls_avoided=2,
                )

            receipt = compiler.record_execution(
                identity,
                bound,
                ok=True,
                reverified=True,
                verifier_receipt_hash=digest("verify-gamma"),
                provider_calls_avoided=2,
            )
            self.assertTrue(receipt.ok)
            self.assertTrue(receipt.reverified)
            self.assertEqual(receipt.provider_calls_avoided, 2)
            self.assertEqual(receipt.executed_actions, len(SEARCH_GRAPH))
            self.assertEqual(len(compiler.memory.events(compiler._session_id(identity))), 4)

    def test_task_reference_is_reference_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            compiler = self.compiler(Path(temp))
            with self.assertRaisesRegex(ValueError, "raw authority"):
                compiler.record_verified_experience(
                    compatibility(),
                    [{"action": "search", "query": "task"}],
                    parameters={"task": "task"},
                    task_reference={"instruction_hash": digest("task"), "payload": "raw task body"},
                    verifier_receipt_hash=digest("verify"),
                )


if __name__ == "__main__":
    unittest.main()
