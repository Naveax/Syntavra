from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.autonomous_agent import AgentMode, AgentTask, AutonomousCodingAgent, PatchProposal
from syntavra_runtime.inference_skip_cache import InferenceSkipCache, build_identity, repository_state_fingerprint


PATCH_GOOD = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)
PATCH_BAD_BUT_APPLIES = (
    "diff --git a/value.txt b/value.txt\n"
    "--- a/value.txt\n"
    "+++ b/value.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+bad\n"
)


class CountingProvider:
    def __init__(self, patch: str = PATCH_GOOD):
        self.patch = patch
        self.calls = 0

    def propose(self, task, context, previous_failure):
        self.calls += 1
        return PatchProposal(self.patch, "provider patch", estimated_tokens=321, estimated_cost=0.01)


class ForbiddenProvider:
    def __init__(self):
        self.calls = 0

    def propose(self, task, context, previous_failure):
        self.calls += 1
        raise AssertionError("provider must not be called on a verified exact cache hit")


class InferenceSkipCacheTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "value.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "syntavra@example.invalid"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "Syntavra Test"], cwd=project, check=True)
        subprocess.run(["git", "add", "value.txt"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=project, check=True)
        return project

    @staticmethod
    def _task(verifier_suffix: str = "") -> AgentTask:
        verifier = (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('value.txt').read_text(encoding='utf-8') == 'new\\n'" + verifier_suffix,
        )
        return AgentTask(
            "change old to new",
            verifier,
            mode=AgentMode.SAFE_AUTONOMOUS,
            max_attempts=2,
            timeout_seconds=30,
            retain_workspace=False,
            metadata={
                "verifier_discovery": [
                    {"name": "exact-python", "argv": list(verifier)}
                ],
                "security_policy_fingerprint": "security-v1",
                "provider_policy_fingerprint": "provider-v1",
            },
        )

    def test_second_exact_run_uses_zero_provider_calls_and_reverifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            first_provider = CountingProvider()
            first = AutonomousCodingAgent(project, state).execute(self._task(), first_provider, authorized=True)
            self.assertTrue(first.ok, first)
            self.assertEqual(first_provider.calls, 1)
            self.assertEqual(first.total_tokens, 321)
            self.assertTrue(first.context["inference_skip"]["recorded_verified"])

            forbidden = ForbiddenProvider()
            second = AutonomousCodingAgent(project, state).execute(self._task(), forbidden, authorized=True)
            self.assertTrue(second.ok, second)
            self.assertEqual(forbidden.calls, 0)
            self.assertEqual(second.total_tokens, 0)
            self.assertEqual(second.total_cost, 0.0)
            self.assertTrue(second.context["inference_skip"]["hit"])
            self.assertEqual(second.context["inference_skip"]["provider_calls_avoided"], 1)
            self.assertIn("inference-skip", second.stop_reason)
            self.assertTrue(second.attempts[0].verifier and second.attempts[0].verifier.ok)

    def test_dirty_repository_disables_skip_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            first_provider = CountingProvider()
            first = AutonomousCodingAgent(project, state).execute(self._task(), first_provider, authorized=True)
            self.assertTrue(first.ok)

            (project / "untracked.txt").write_text("new source state\n", encoding="utf-8")
            provider = CountingProvider()
            second = AutonomousCodingAgent(project, state).execute(self._task(), provider, authorized=True)
            self.assertTrue(second.ok)
            self.assertEqual(provider.calls, 1)
            self.assertFalse(second.context["inference_skip"]["eligible"])
            self.assertEqual(second.context["inference_skip"]["repository_cache_reason"], "dirty-source-worktree")

    def test_verifier_identity_change_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            first = AutonomousCodingAgent(project, state).execute(self._task(), CountingProvider(), authorized=True)
            self.assertTrue(first.ok)

            provider = CountingProvider()
            changed_task = self._task("; assert True")
            second = AutonomousCodingAgent(project, state).execute(changed_task, provider, authorized=True)
            self.assertTrue(second.ok)
            self.assertEqual(provider.calls, 1)
            self.assertFalse(second.context["inference_skip"]["hit"])

    def test_policy_fingerprint_change_is_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            task = self._task()
            first = AutonomousCodingAgent(project, state).execute(task, CountingProvider(), authorized=True)
            self.assertTrue(first.ok)

            changed = AgentTask(
                task.instruction,
                task.verifier,
                mode=task.mode,
                max_attempts=task.max_attempts,
                timeout_seconds=task.timeout_seconds,
                metadata={**task.metadata, "security_policy_fingerprint": "security-v2"},
            )
            provider = CountingProvider()
            second = AutonomousCodingAgent(project, state).execute(changed, provider, authorized=True)
            self.assertTrue(second.ok)
            self.assertEqual(provider.calls, 1)

    def test_cached_patch_that_fails_verifier_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            task = self._task()
            first = AutonomousCodingAgent(project, state).execute(task, CountingProvider(), authorized=True)
            self.assertTrue(first.ok)

            fingerprint = repository_state_fingerprint(project)
            policy_material = {
                "security_policy_fingerprint": task.metadata["security_policy_fingerprint"],
                "provider_policy_fingerprint": task.metadata["provider_policy_fingerprint"],
                "verifier_discovery": task.metadata["verifier_discovery"],
            }
            import json
            policy_hash = hashlib.sha256(json.dumps(policy_material, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            identity = build_identity(
                project=project,
                repository_fingerprint=fingerprint,
                instruction=task.instruction,
                verifier=task.verifier,
                mode=task.mode.value,
                policy_fingerprint=policy_hash,
            )
            cache = InferenceSkipCache(state / "inference-skip.sqlite3")
            bad_hash = hashlib.sha256(PATCH_BAD_BUT_APPLIES.encode("utf-8")).hexdigest()
            with sqlite3.connect(cache.path) as db:
                db.execute(
                    "UPDATE inference_skip_cache SET patch=?,patch_hash=? WHERE cache_key=?",
                    (PATCH_BAD_BUT_APPLIES, bad_hash, identity.key),
                )
                db.commit()

            one_attempt = AgentTask(
                task.instruction,
                task.verifier,
                mode=task.mode,
                max_attempts=1,
                timeout_seconds=task.timeout_seconds,
                metadata=task.metadata,
            )
            forbidden = ForbiddenProvider()
            failed = AutonomousCodingAgent(project, state).execute(one_attempt, forbidden, authorized=True)
            self.assertFalse(failed.ok)
            self.assertEqual(forbidden.calls, 0)
            row = cache.inspect(identity.key)
            self.assertIsNotNone(row)
            self.assertEqual(int(row["valid"]), 0)
            self.assertEqual(row["invalid_reason"], "cached-patch-verifier-failed")

    def test_multi_verifier_plan_is_not_cached_before_post_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self._project(root)
            state = root / "state"
            task = self._task()
            multi = AgentTask(
                task.instruction,
                task.verifier,
                mode=task.mode,
                max_attempts=task.max_attempts,
                timeout_seconds=task.timeout_seconds,
                metadata={
                    **task.metadata,
                    "verifier_discovery": [
                        task.metadata["verifier_discovery"][0],
                        {"name": "second", "argv": [sys.executable, "-c", "assert True"]},
                    ],
                },
            )
            first_provider = CountingProvider()
            first = AutonomousCodingAgent(project, state).execute(multi, first_provider, authorized=True)
            self.assertTrue(first.ok)
            self.assertEqual(first_provider.calls, 1)
            self.assertFalse(first.context["inference_skip"]["eligible"])

            second_provider = CountingProvider()
            second = AutonomousCodingAgent(project, state).execute(multi, second_provider, authorized=True)
            self.assertTrue(second.ok)
            self.assertEqual(second_provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
