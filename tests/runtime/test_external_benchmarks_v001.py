from __future__ import annotations

import unittest

from syntavra_runtime.external_benchmarks import (
    ExternalBenchmarkGate,
    ExternalBenchmarkReceipt,
    ExternalSuiteRegistry,
)


class ExternalBenchmarksV001Tests(unittest.TestCase):
    @staticmethod
    def _receipt(index: int, arm: str, suite: str = "swe-bench") -> ExternalBenchmarkReceipt:
        baseline = arm == "baseline"
        return ExternalBenchmarkReceipt(
            receipt_id=f"{suite}-{index}-{arm}",
            suite_id=suite,
            task_id=f"task-{index:03d}",
            arm=arm,
            repetition=index + 1,
            dataset_version="verified-dataset-v1",
            harness_commit="a" * 40,
            verifier_commit="b" * 40,
            environment_image_digest="sha256:" + "c" * 64,
            repository_commit="d" * 40 if suite == "swe-bench" else "",
            provider="openai",
            model="test-model",
            model_config_hash="e" * 64,
            result_artifact_hash=("f" if baseline else "1") * 64,
            raw_provider_receipt_hash=("2" if baseline else "3") * 64,
            quality_score=0.90 if baseline else 0.91,
            success=True,
            input_tokens=1000 if baseline else 650,
            cached_input_tokens=0 if baseline else 50,
            output_tokens=200,
            cost_usd=0.020 if baseline else 0.014,
            wall_time_ms=1000 if baseline else 900,
            recursive_calls=0,
            synthetic=False,
            metadata={},
        )

    def test_registry_names_real_external_suites_without_claiming_results(self) -> None:
        manifest = ExternalSuiteRegistry.manifest()
        suites = {row["suite_id"] for row in manifest["suites"]}
        self.assertEqual(
            suites,
            {"swe-bench", "oolong", "longbench-v2", "infinitebench", "recursive-long-context"},
        )
        self.assertIn("not external benchmark results", manifest["claim_boundary"])

    def test_real_paired_receipts_open_evidence_gate_for_manual_review(self) -> None:
        rows = []
        for index in range(30):
            rows.extend((self._receipt(index, "baseline"), self._receipt(index, "syntavra")))
        result = ExternalBenchmarkGate.evaluate(rows, suite_id="swe-bench")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["metrics"]["pairs"], 30)
        self.assertLess(result["metrics"]["mean_token_ratio"], 1.0)
        self.assertLess(result["metrics"]["mean_cost_ratio"], 1.0)
        self.assertLess(result["metrics"]["mean_wall_time_ratio"], 1.0)
        self.assertEqual(result["public_superiority"], "ELIGIBLE_FOR_MANUAL_REVIEW")

    def test_suite_label_without_exact_harness_evidence_fails_closed(self) -> None:
        row = self._receipt(0, "syntavra")
        invalid = ExternalBenchmarkReceipt(**{
            **row.__dict__,
            "harness_commit": "latest",
            "environment_image_digest": "ubuntu-latest",
        })
        result = ExternalBenchmarkGate.evaluate([invalid])
        self.assertFalse(result["ok"])
        self.assertIn("invalid-receipts", result["reasons"])
        self.assertEqual(result["public_superiority"], "EXTERNAL_SUPERIORITY_NOT_PROVEN")

    def test_synthetic_receipts_never_count(self) -> None:
        row = self._receipt(0, "baseline")
        synthetic = ExternalBenchmarkReceipt(**{**row.__dict__, "synthetic": True})
        result = ExternalBenchmarkGate.evaluate([synthetic])
        self.assertFalse(result["ok"])
        self.assertIn("synthetic-receipts-present", result["reasons"])


if __name__ == "__main__":
    unittest.main()
