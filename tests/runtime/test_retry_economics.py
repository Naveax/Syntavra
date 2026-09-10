from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.autonomous_agent import AgentMode, AgentTask, AutonomousCodingAgent, PatchProposal
from syntavra_runtime.execution_sandbox import SandboxBackend, SandboxPolicy
from syntavra_runtime.retry_economics import (
    RetryAction,
    RetryEconomicsGovernor,
    WorkspaceStateFingerprint,
    workspace_state_fingerprint,
)
from syntavra_runtime.sandbox_runtime import HardenedSandboxBroker


class PortableTestBroker(HardenedSandboxBroker):
    """Deterministic process boundary for retry-policy tests."""

    def backend(self, policy: SandboxPolicy) -> SandboxBackend:
        return SandboxBackend(
            name="retry-economics-test-boundary",
            platform=sys.platform,
            available=True,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=(),
            command_prefix=(),
            detail="test-only deterministic backend",
        )


class CountingProvider:
    def __init__(self, patches: list[str]):
        self.patches = list(patches)
        self.calls = 0
        self.failures: list[str] = []

    def propose(self, task, context, previous_failure):
        del task, context
        self.calls += 1
        self.failures.append(str((previous_failure or {}).get("fingerprint") or ""))
        index = min(self.calls - 1, len(self.patches) - 1)
        return PatchProposal(
            self.patches[index],
            rationale=f"repair-{self.calls}",
            estimated_tokens=100,
            estimated_cost=0.01,
        )


def _patch(before: str, after: str) -> str:
    return (
        "diff --git a/value.txt b/value.txt\n"
        "--- a/value.txt\n"
        "+++ b/value.txt\n"
        "@@ -1 +1 @@\n"
        f"-{before}\n"
        f"+{after}\n"
    )


def _git_project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "value.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "retry@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Retry Test"], cwd=project, check=True)
    subprocess.run(["git", "add", "value.txt"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=project, check=True)
    return project


@unittest.skipIf(shutil.which("git") is None, "git is required")
class RetryEconomicsTests(unittest.TestCase):
    def test_governor_stops_only_exact_failure_state_repeat(self) -> None:
        governor = RetryEconomicsGovernor()
        first_state = WorkspaceStateFingerprint("state-a", "test", True)
        second_state = WorkspaceStateFingerprint("state-b", "test", True)
        failure = {"fingerprint": "failure-a"}

        self.assertEqual(governor.assess(None, first_state).action, RetryAction.ALLOW_INITIAL)
        self.assertEqual(governor.assess(failure, first_state).action, RetryAction.ALLOW_REPAIR)
        repeated = governor.assess(failure, first_state)
        self.assertEqual(repeated.action, RetryAction.STOP_EXACT_REPEAT)
        self.assertFalse(repeated.allow_provider)
        self.assertEqual(repeated.provider_calls_avoided, 1)

        changed = governor.assess(failure, second_state)
        self.assertEqual(changed.action, RetryAction.ALLOW_REPAIR)
        self.assertTrue(changed.allow_provider)

        uncertain = governor.assess(failure, WorkspaceStateFingerprint("", "test", False, "unavailable"))
        self.assertEqual(uncertain.action, RetryAction.ALLOW_UNCERTAIN)
        self.assertTrue(uncertain.allow_provider)

    def test_workspace_fingerprint_changes_with_exact_file_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.txt").write_text("one\n", encoding="utf-8")
            before = workspace_state_fingerprint(root)
            (root / "a.txt").write_text("two\n", encoding="utf-8")
            after = workspace_state_fingerprint(root)
            self.assertTrue(before.complete)
            self.assertTrue(after.complete)
            self.assertNotEqual(before.digest, after.digest)

    def test_exact_unchanged_failure_state_stops_before_third_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _git_project(root)
            state = root / "state"
            provider = CountingProvider([_patch("one", "two"), _patch("one", "three"), _patch("one", "four")])
            agent = AutonomousCodingAgent(project, state, sandbox=PortableTestBroker(state))
            verifier = (
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path('value.txt').write_text('one\\n', encoding='utf-8'); print('stable-failure', file=sys.stderr); raise SystemExit(1)",
            )
            receipt = agent.execute(
                AgentTask(
                    instruction="make verifier pass",
                    verifier=verifier,
                    mode=AgentMode.SAFE_AUTONOMOUS,
                    max_attempts=4,
                ),
                provider,
                authorized=True,
            )

            self.assertEqual(provider.calls, 2)
            self.assertEqual(len(receipt.attempts), 2)
            self.assertEqual(
                receipt.stop_reason,
                "retry-economics: exact failure already presented on unchanged workspace state",
            )
            retry = receipt.context["retry_economics"]
            self.assertEqual(retry["provider_calls_avoided"], 1)
            self.assertEqual(retry["decisions"][-1]["action"], RetryAction.STOP_EXACT_REPEAT.value)

    def test_same_failure_on_changed_state_remains_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _git_project(root)
            state = root / "state"
            provider = CountingProvider([_patch("one", "two"), _patch("two", "three"), _patch("three", "four")])
            agent = AutonomousCodingAgent(project, state, sandbox=PortableTestBroker(state))
            verifier = (
                sys.executable,
                "-c",
                "import sys; print('stable-failure', file=sys.stderr); raise SystemExit(1)",
            )
            receipt = agent.execute(
                AgentTask(
                    instruction="keep making stateful repairs",
                    verifier=verifier,
                    mode=AgentMode.SAFE_AUTONOMOUS,
                    max_attempts=3,
                ),
                provider,
                authorized=True,
            )

            self.assertEqual(provider.calls, 3)
            self.assertEqual(len(receipt.attempts), 3)
            self.assertEqual(receipt.stop_reason, "attempt limit reached")
            retry = receipt.context["retry_economics"]
            self.assertEqual(retry["provider_calls_avoided"], 0)
            repair_actions = [row["action"] for row in retry["decisions"] if row["action"] == RetryAction.ALLOW_REPAIR.value]
            self.assertEqual(len(repair_actions), 2)


if __name__ == "__main__":
    unittest.main()
