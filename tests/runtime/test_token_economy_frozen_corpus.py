from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from benchmarks.token_economy_frozen_corpus import (
    materialize,
    portable_projection,
    verify_initial_failures,
)
from syntavra_runtime.signalbench import SignalBenchProtocol, TaskSpec


@unittest.skipIf(shutil.which("git") is None, "git is required")
class FrozenTokenEconomyCorpusTests(unittest.TestCase):
    def _materialize(self, root: Path):
        manifest = materialize(root)
        self.assertEqual(manifest["workload_count"], 10)
        self.assertEqual([row["task_id"].split("-", 1)[0] for row in manifest["tasks"]], [f"B{i}" for i in range(10)])
        return manifest

    def test_two_materialization_roots_have_identical_portable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = self._materialize(base / "first")
            second = self._materialize(base / "second")
            self.assertEqual(first["portable_identity_sha256"], second["portable_identity_sha256"])
            self.assertEqual(portable_projection(first), portable_projection(second))

            first_repositories = [row["repository"] for row in first["tasks"]]
            second_repositories = [row["repository"] for row in second["tasks"]]
            self.assertNotEqual(first_repositories, second_repositories)

    def test_every_initial_fixture_fails_its_immutable_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._materialize(Path(temporary) / "corpus")
            gate = verify_initial_failures(manifest)
            self.assertTrue(gate["ok"], gate)
            self.assertEqual(gate["checked"], 10)
            self.assertEqual(gate["unexpected_initial_passes"], [])

    def test_signalbench_repository_identity_is_exact_and_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._materialize(Path(temporary) / "corpus")
            for row in manifest["tasks"]:
                with self.subTest(task=row["task_id"]):
                    task = TaskSpec(
                        **{
                            **row,
                            "verifier": tuple(row["verifier"]),
                            "permissions": tuple(row["permissions"]),
                        }
                    )
                    self.assertEqual(SignalBenchProtocol.validate_task(task), [])
                    self.assertEqual(
                        __import__("syntavra_runtime.signalbench", fromlist=["SignalBenchRunner"]).SignalBenchRunner._frozen_repository_reasons(task),
                        [],
                    )
                    self.assertTrue(task.repository_tree)
                    self.assertTrue(task.repository_commit)
                    self.assertTrue(task.metadata["frozen_workload_identity"].startswith("sha256:"))
                    self.assertEqual(task.metadata["verifier_transport"], "inline-immutable-task-contract")

    def test_portable_identity_excludes_local_repository_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = self._materialize(base / "a")
            second = self._materialize(base / "b")
            for left, right in zip(first["tasks"], second["tasks"], strict=True):
                self.assertNotEqual(left["repository"], right["repository"])
                self.assertEqual(left["repository_tree"], right["repository_tree"])
                self.assertEqual(left["repository_commit"], right["repository_commit"])
                self.assertEqual(
                    left["metadata"]["frozen_workload_identity"],
                    right["metadata"]["frozen_workload_identity"],
                )


if __name__ == "__main__":
    unittest.main()
