from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import asdict
from pathlib import Path

from syntavra_runtime.signalbench import ArmSpec, RunResult, SignalBenchProtocol, SignalBenchRunner, TASK_FAMILIES, TaskSpec, _safe_environment
from syntavra_runtime.cli import build_parser
from syntavra_runtime.signalbench_hardened import HardwareIdentity, UsageReceipt

ROOT = Path(__file__).resolve().parents[2]


def _validation_runner() -> SignalBenchRunner:
    return SignalBenchRunner(Path(tempfile.gettempdir()) / "syntavra-signalbench-product-validation")


class SignalBenchPythonProductV1Tests(unittest.TestCase):
    def _repo(self, root: Path) -> tuple[Path, str, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "signalbench@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "SignalBench Fixture"], check=True)
        (repo / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--", "fixture.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
        return repo, commit, tree

    @staticmethod
    def _arm(arm_id: str, version: str = "1.2.3") -> ArmSpec:
        return ArmSpec(arm_id, "host", (sys.executable, "adapter.py"), version, "fixture-model", "high", 200000)

    def test_contract_and_enforcement_surfaces_exist(self) -> None:
        contract = json.loads((ROOT / "contracts/python/signalbench-python-product-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["claim"], "SIGNALBENCH_PYTHON_PRODUCT_V1")
        self.assertTrue(contract["frozen_identity"]["repository_git_tree_must_match"])
        self.assertTrue(contract["measurement"]["sealed_usage_receipts_required"])
        self.assertFalse(contract["ownership_policy"]["new_public_cli_route_added"])
        self.assertIn("external superiority", contract["claim_boundary"])

    def test_product_validation_rejects_templates(self) -> None:
        task = TaskSpec("t", "known-edit", "fix", "/tmp/missing", "pin-exact-git-tree-sha", (sys.executable, "-c", "pass"))
        arms = [self._arm("base", "pin-exact-version"), self._arm("candidate")]
        report = _validation_runner().validate_product([task], arms)
        self.assertFalse(report["ok"])
        joined = " ".join(report["reasons"])
        self.assertIn("repository-tree-not-exact", joined)
        self.assertIn("version-not-exact", joined)

    def test_exact_tree_and_clean_worktree_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo, commit, tree = self._repo(Path(temp))
            task = TaskSpec("t", "known-edit", "fix", str(repo), tree, (sys.executable, "-c", "pass"), repository_commit=commit)
            arms = [self._arm("base"), self._arm("candidate")]
            self.assertTrue(_validation_runner().validate_product([task], arms)["ok"])
            mismatch = TaskSpec(**{**asdict(task), "repository_tree": "0" * len(tree)})
            self.assertIn("task:t:repository-tree-mismatch", _validation_runner().validate_product([mismatch], arms)["reasons"])
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            self.assertIn("task:t:repository-worktree-dirty", _validation_runner().validate_product([task], arms)["reasons"])

    @staticmethod
    def _result(arm: str, repetition: int, quota: float, receipt_hash: str = "") -> RunResult:
        values = dict(
  run_id=f"{arm}-{repetition}", task_id="task", arm_id=arm, repetition=repetition,
  success=True, verifier_success=True, verified_work=1.0, wall_seconds=1.0, exit_code=0,
  fresh_input_tokens=100, cached_input_tokens=20, output_tokens=10, reasoning_tokens=5,
  quota_cost=quota, model_turns=1, tool_calls=1, wait_calls=0, compactions=0,
  security_regressions=0, verifier_skips=0, repository_tree="a" * 40,
  prompt_hash="b" * 64, verifier_hash="c" * 64, permissions_hash="d" * 64,
  cache_mode="cold", artifact_dir="artifact", provider_observed=True, provider="openai",
  model="fixture-model", request_id_hash=(f"{repetition:064x}" if arm == "base" else f"{repetition + 1000:064x}"),
  provider_receipt_hash="1" * 64, arm_version="1.2.3" if arm == "base" else "4.5.6",
  reasoning="high", context_window=200000, hardware_hash="2" * 64,
  provider_response_hash=("3" if arm == "base" else "4") * 64,
  usage_receipt_hash=receipt_hash,
            repository_commit="6" * 40, task_hash="5" * 64, timeout_seconds=1200.0,
        )
        provisional = RunResult(**values)
        receipt = UsageReceipt.seal(
  task_id=provisional.task_id, arm_id=provisional.arm_id, repetition=provisional.repetition,
  cache_mode=provisional.cache_mode, provider=provisional.provider,
  request_id_hash=provisional.request_id_hash, provider_response_hash=provisional.provider_response_hash,
  fresh_input_tokens=provisional.fresh_input_tokens, cached_input_tokens=provisional.cached_input_tokens,
  output_tokens=provisional.output_tokens, reasoning_tokens=provisional.reasoning_tokens,
  quota_cost=float(provisional.quota_cost), hardware_hash=provisional.hardware_hash,
        )
        return RunResult(**{**values, "usage_receipt_hash": receipt.receipt_hash})

    def test_public_compare_uses_hardened_receipt_authority(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        result = SignalBenchRunner.compare(rows, baseline_arm="base", candidate_arm="candidate")
        self.assertTrue(result["claimable_superiority"])
        self.assertEqual(result["comparison_authority"], "HardenedSignalBench.compare")
        self.assertEqual(result["valid_pairs"], 10)
        self.assertFalse(result["identity_mismatches"])
        self.assertFalse(result["receipt_errors"])

    def test_identity_drift_and_receipt_tampering_fail_closed(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        bad_identity = RunResult(**{**asdict(rows[-1]), "hardware_hash": "9" * 64})
        identity_rows = rows[:-1] + [bad_identity]
        identity = SignalBenchRunner.compare(identity_rows, baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(identity["claimable_superiority"])
        self.assertTrue(identity["identity_mismatches"])
        tampered = RunResult(**{**asdict(rows[-1]), "quota_cost": 0.5})
        tampered_rows = rows[:-1] + [tampered]
        receipt = SignalBenchRunner.compare(tampered_rows, baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(receipt["claimable_superiority"])
        self.assertTrue(receipt["receipt_errors"])

    def test_legacy_fixture_validate_remains_available_but_product_validate_is_strict(self) -> None:
        task = TaskSpec("t", "known-edit", "fix", "/tmp/missing", "tree", (sys.executable, "-c", "pass"))
        arms = [self._arm("base"), self._arm("candidate")]
        self.assertTrue(_validation_runner().validate([task], arms)["ok"])
        self.assertFalse(_validation_runner().validate_product([task], arms)["ok"])

    def test_public_cli_validate_route_is_product_grade_without_new_route(self) -> None:
        cli = (ROOT / "syntavra_runtime/cli.py").read_text(encoding="utf-8")
        self.assertIn("runner.validate_product", cli)
        self.assertIn('sub.add_parser("signalbench")', cli)
        self.assertNotIn('sub.add_parser("signalbench-product")', cli)


    def test_shipped_task_templates_use_canonical_families(self) -> None:
        tasks = SignalBenchRunner.load_tasks(ROOT / "benchmarks/signalbench/tasks.example.json")
        self.assertTrue(tasks)
        self.assertEqual(sorted({task.family for task in tasks if task.family not in TASK_FAMILIES}), [])

    def test_missing_and_duplicate_pair_data_fail_closed(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        missing = SignalBenchRunner.compare(rows[:-1], baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(missing["claimable_superiority"])
        self.assertTrue(any(item.get("reason") == "missing-arm" for item in missing["invalid"]))
        duplicate = SignalBenchRunner.compare(rows + [rows[-1]], baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(duplicate["claimable_superiority"])
        self.assertTrue(any(item.get("reason") == "duplicate-result-key" for item in duplicate["invalid"]))

    def test_usage_receipt_hash_fields_require_lowercase_sha256_hex(self) -> None:
        row = self._result("base", 1, 1.0)
        receipt = row.usage_receipt()
        self.assertIsNotNone(receipt)
        bad = UsageReceipt(**{**asdict(receipt), "request_id_hash": "g" * 64})
        self.assertIn("provider-evidence-incomplete", bad.validate())
        upper = UsageReceipt(**{**asdict(receipt), "hardware_hash": "A" * 64})
        self.assertIn("hardware-hash-invalid", upper.validate())

    def test_compare_dispatch_happens_before_runner_construction(self) -> None:
        args = build_parser().parse_args([
            "signalbench", "compare", "--results", "results.json",
            "--baseline-arm", "base", "--candidate-arm", "candidate",
        ])
        self.assertEqual(args.output_root, "signalbench-results")
        self.assertEqual(args.seed, 1337)
        cli = (ROOT / "syntavra_runtime/cli.py").read_text(encoding="utf-8")
        block = cli[cli.index("def command_signalbench"):cli.index("def command_claim")]
        self.assertLess(block.index('if args.action == "compare"'), block.index("runner = SignalBenchRunner"))

    def test_manifest_route_rejects_placeholder_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.json"
            args = build_parser().parse_args([
                "signalbench", "manifest",
                "--tasks", str(ROOT / "benchmarks/signalbench/tasks.example.json"),
                "--arms", str(ROOT / "benchmarks/signalbench/arms.example.json"),
                "--output-root", str(Path(temp) / "runs"),
                "--output", str(output),
            ])
            self.assertEqual(args.func(args), 3)
            self.assertFalse(output.exists())


    def test_exact_commit_and_git_workspace_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, commit, tree = self._repo(root)
            task = TaskSpec("t", "known-edit", "fix", str(repo), tree, (sys.executable, "-c", "pass"), repository_commit=commit)
            arms = [self._arm("base"), self._arm("candidate")]
            self.assertTrue(_validation_runner().validate_product([task], arms)["ok"])
            wrong = TaskSpec(**{**asdict(task), "repository_commit": "0" * len(commit)})
            self.assertIn("task:t:repository-commit-mismatch", _validation_runner().validate_product([wrong], arms)["reasons"])
            workspace = root / "workspace"
            SignalBenchRunner._copy_repository(task, repo, workspace)
            self.assertTrue((workspace / ".git").is_dir())
            self.assertEqual(subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip(), commit)
            self.assertEqual(subprocess.check_output(["git", "-C", str(workspace), "remote"], text=True).strip(), "")

    def test_host_environment_is_explicitly_isolated(self) -> None:
        with mock.patch.dict(os.environ, {"SIGNALBENCH_TEST_SECRET": "secret"}, clear=False):
            self.assertNotIn("SIGNALBENCH_TEST_SECRET", _safe_environment())
            self.assertEqual(_safe_environment(inherit=("SIGNALBENCH_TEST_SECRET",))["SIGNALBENCH_TEST_SECRET"], "secret")

    def test_symmetric_task_drift_and_arm_version_drift_fail_closed(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        symmetric = list(rows)
        symmetric[-2] = RunResult(**{**asdict(symmetric[-2]), "repository_commit": "7" * 40})
        symmetric[-1] = RunResult(**{**asdict(symmetric[-1]), "repository_commit": "7" * 40})
        report = SignalBenchRunner.compare(symmetric, baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(report["claimable_superiority"])
        self.assertTrue(any(item.get("scope") == "task-global" for item in report["identity_mismatches"]))
        changed = list(rows)
        changed[-1] = RunResult(**{**asdict(changed[-1]), "arm_version": "9.9.9"})
        report = SignalBenchRunner.compare(changed, baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(report["claimable_superiority"])
        self.assertTrue(any(item.get("scope") == "arm-global" for item in report["identity_mismatches"]))

    def test_provider_request_reuse_fails_closed(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        reused = RunResult(**{**asdict(rows[-2]), "request_id_hash": rows[0].request_id_hash})
        receipt = UsageReceipt.seal(
            task_id=reused.task_id, arm_id=reused.arm_id, repetition=reused.repetition,
            cache_mode=reused.cache_mode, provider=reused.provider, request_id_hash=reused.request_id_hash,
            provider_response_hash=reused.provider_response_hash, fresh_input_tokens=reused.fresh_input_tokens,
            cached_input_tokens=reused.cached_input_tokens, output_tokens=reused.output_tokens,
            reasoning_tokens=reused.reasoning_tokens, quota_cost=float(reused.quota_cost), hardware_hash=reused.hardware_hash,
        )
        reused = RunResult(**{**asdict(reused), "usage_receipt_hash": receipt.receipt_hash})
        report = SignalBenchRunner.compare(rows[:-2] + [reused, rows[-1]], baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(report["claimable_superiority"])
        self.assertTrue(any("provider-request-reused" in item.get("reasons", []) for item in report["receipt_errors"]))

    def test_packaged_external_adapter_is_the_template_authority(self) -> None:
        arms = json.loads((ROOT / "benchmarks/signalbench/arms.example.json").read_text(encoding="utf-8"))["arms"]
        for arm in arms:
            self.assertIn("syntavra_runtime.signalbench_external_adapter", arm["command"])
        self.assertTrue((ROOT / "syntavra_runtime/signalbench_external_adapter.py").is_file())


if __name__ == "__main__":
    unittest.main()
