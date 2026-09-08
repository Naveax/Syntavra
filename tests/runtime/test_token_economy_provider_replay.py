from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.token_economy_provider_replay import _load_arms, build_schedule, plan
from benchmarks.token_economy_frozen_corpus import materialize
from syntavra_runtime.signalbench import ArmSpec, TaskSpec


class TokenEconomyProviderReplayTests(unittest.TestCase):
    def _arms_path(self, root: Path, *, secret: bool = False) -> Path:
        environment = {"OPENAI_API_KEY": "must-not-live-here"} if secret else {}
        value = {
            "arms": [
                {
                    "arm_id": "baseline",
                    "category": "baseline",
                    "command": ["python", "-c", "raise SystemExit(0)"],
                    "version": "baseline-v1",
                    "model": "frozen-model",
                    "reasoning": "frozen-effort",
                    "context_window": 32768,
                    "environment": environment,
                },
                {
                    "arm_id": "candidate",
                    "category": "candidate",
                    "command": ["python", "-c", "raise SystemExit(0)"],
                    "version": "candidate-v1",
                    "model": "frozen-model",
                    "reasoning": "frozen-effort",
                    "context_window": 32768,
                    "environment": {},
                },
            ]
        }
        path = root / "arms.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    @staticmethod
    def _tasks(manifest: dict[str, object]) -> list[TaskSpec]:
        return [
            TaskSpec(
                **{
                    **row,
                    "verifier": tuple(row["verifier"]),
                    "permissions": tuple(row["permissions"]),
                }
            )
            for row in manifest["tasks"]
        ]

    def test_schedule_respects_frozen_workload_variants_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = materialize(root / "corpus")
            tasks = self._tasks(manifest)
            arms = _load_arms(self._arms_path(root))
            schedule = build_schedule(tasks, arms, 3)
            self.assertEqual(len(schedule), 84)
            self.assertEqual(len(schedule) // 2, 42)
            by_task_mode = {(row["task_id"], row["cache_mode"]) for row in schedule}
            self.assertIn(("B0-deterministic-repeat", "cold"), by_task_mode)
            self.assertIn(("B0-deterministic-repeat", "warm"), by_task_mode)
            self.assertEqual(
                {mode for task, mode in by_task_mode if task == "B1-tiny-edit"},
                {"cold"},
            )
            self.assertEqual(
                {mode for task, mode in by_task_mode if task == "B8-warm-repo"},
                {"warm"},
            )

    def test_repetition_floor_is_fail_closed(self) -> None:
        task = TaskSpec(
            task_id="B0-deterministic-repeat",
            family="known-edit",
            prompt="x",
            repository="/tmp/not-used",
            repository_tree="0" * 40,
            repository_commit="1" * 40,
            verifier=("python", "-c", "raise SystemExit(1)"),
            metadata={"frozen_workload_identity": "sha256:" + "2" * 64},
        )
        arms = [
            ArmSpec("baseline", "baseline", ("python",), "v1", "m", "r", 1024),
            ArmSpec("candidate", "candidate", ("python",), "v2", "m", "r", 1024),
        ]
        with self.assertRaisesRegex(ValueError, "at least three repetitions"):
            build_schedule([task], arms, 2)

    def test_explicit_credential_values_are_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "credential-like environment"):
                _load_arms(self._arms_path(root, secret=True))

    def test_plan_is_offline_and_receipt_claim_stays_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = plan(root / "corpus", self._arms_path(root), 3)
            self.assertEqual(value["claim_boundary"], "REPLAY_PLAN_ONLY_NOT_PROVIDER_PROOF")
            self.assertEqual(value["workload_count"], 10)
            self.assertEqual(value["arm_count"], 2)
            self.assertEqual(value["scheduled_runs"], 84)
            self.assertEqual(value["scheduled_pairs"], 42)
            self.assertEqual(len(value["plan_sha256"]), 64)
            self.assertNotIn("provider_proof_complete", value)


if __name__ == "__main__":
    unittest.main()
