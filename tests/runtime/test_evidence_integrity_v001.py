from __future__ import annotations

import datetime as dt
import unittest

from syntavra_runtime.evidence_integrity import ExternalEvidenceIntegrityGate
from syntavra_runtime.external_benchmarks import ExternalBenchmarkReceipt
from syntavra_runtime.live_certification import LiveIntegrationReceipt


NOW = dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.timezone.utc)


class ExternalEvidenceIntegrityV001Tests(unittest.TestCase):
    @staticmethod
    def benchmark(index: int, arm: str) -> ExternalBenchmarkReceipt:
        return ExternalBenchmarkReceipt(
            receipt_id=f"benchmark-{index}-{arm}",
            suite_id="swe-bench",
            task_id=f"task-{index}",
            arm=arm,
            repetition=index + 1,
            dataset_version="verified-v1",
            harness_commit="a" * 40,
            verifier_commit="b" * 40,
            environment_image_digest="sha256:" + "c" * 64,
            repository_commit="d" * 40,
            provider="openai",
            model="test-model",
            model_config_hash="e" * 64,
            result_artifact_hash=(f"{index:064x}"[-64:] if arm == "baseline" else f"{index + 1000:064x}"[-64:]),
            raw_provider_receipt_hash=(f"{index + 2000:064x}"[-64:] if arm == "baseline" else f"{index + 3000:064x}"[-64:]),
            quality_score=0.9,
            success=True,
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
            cost_usd=0.01,
            wall_time_ms=100,
            recursive_calls=0,
            synthetic=False,
            metadata={},
        )

    @staticmethod
    def live(index: int) -> LiveIntegrationReceipt:
        return LiveIntegrationReceipt(
            receipt_id=f"live-{index}",
            integration_id="codex",
            family="host",
            observed_at=(NOW - dt.timedelta(days=index)).isoformat(),
            syntavra_version="0.0.1",
            syntavra_channel="pre-release",
            adapter_version="adapter-v1",
            operating_system=("linux", "windows", "macos")[index % 3],
            runtime_version="python-3.13",
            environment_hash=f"{index + 10:064x}"[-64:],
            config_hash=f"{index + 20:064x}"[-64:],
            harness_commit="c" * 40,
            artifact_hash=f"{index + 30:064x}"[-64:],
            install_succeeded=True,
            doctor_passed=True,
            request_succeeded=True,
            response_succeeded=True,
            streaming_verified=True,
            provider_usage_captured=True,
            tool_routing_verified=True,
            session_continuity_verified=True,
            rollback_verified=True,
            external=True,
            synthetic=False,
            metadata={},
        )

    def test_unique_timestamped_benchmark_receipts_pass_integrity(self) -> None:
        rows = [self.benchmark(index, arm) for index in range(3) for arm in ("baseline", "syntavra")]
        observed = {
            row.receipt_id: (NOW - dt.timedelta(minutes=index)).isoformat()
            for index, row in enumerate(rows)
        }
        result = ExternalEvidenceIntegrityGate.benchmark_receipts(rows, observed_at=observed, now=NOW)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.metrics["unique_result_artifacts"], len(rows))
        self.assertEqual(result.metrics["unique_provider_receipts"], len(rows))

    def test_duplicate_pair_arm_and_artifact_are_rejected(self) -> None:
        first = self.benchmark(0, "baseline")
        duplicate = ExternalBenchmarkReceipt(**{
            **first.__dict__,
            "receipt_id": "different-id",
        })
        observed = {
            first.receipt_id: NOW.isoformat(),
            duplicate.receipt_id: NOW.isoformat(),
        }
        result = ExternalEvidenceIntegrityGate.benchmark_receipts([first, duplicate], observed_at=observed, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("duplicate-pair-arm-runs", result.reasons)
        self.assertIn("duplicate-result-artifacts", result.reasons)
        self.assertIn("duplicate-provider-receipts", result.reasons)

    def test_missing_future_or_stale_timestamp_is_rejected(self) -> None:
        rows = [self.benchmark(index, "baseline") for index in range(3)]
        observed = {
            rows[0].receipt_id: (NOW + dt.timedelta(hours=1)).isoformat(),
            rows[1].receipt_id: (NOW - dt.timedelta(days=400)).isoformat(),
        }
        result = ExternalEvidenceIntegrityGate.benchmark_receipts(rows, observed_at=observed, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("future-dated-receipts", result.reasons)
        self.assertIn("stale-receipts", result.reasons)
        self.assertIn("missing-observed-at", result.reasons)

    def test_live_certification_requires_independent_artifacts_and_environments(self) -> None:
        rows = [self.live(index) for index in range(3)]
        result = ExternalEvidenceIntegrityGate.live_certification_receipts(rows, now=NOW)
        self.assertTrue(result.ok, result)

        duplicate = LiveIntegrationReceipt(**{
            **rows[2].__dict__,
            "receipt_id": "live-duplicate",
            "artifact_hash": rows[0].artifact_hash,
            "environment_hash": rows[0].environment_hash,
            "config_hash": rows[0].config_hash,
            "operating_system": rows[0].operating_system,
        })
        result = ExternalEvidenceIntegrityGate.live_certification_receipts([rows[0], rows[1], duplicate], now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("duplicate-certification-artifacts", result.reasons)
        self.assertIn("duplicate-certification-environments", result.reasons)


if __name__ == "__main__":
    unittest.main()
