from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.product_surface import (
    MCP_PROFILES,
    MENTAL_MODEL,
    MeasuredBenchmarkGate,
    PlatformAdapterRegistry,
    ProductSurface,
    ProviderUsageReceipt,
    ReceiptValidator,
    SessionAnalyticsStore,
    ToolRoutingEnforcer,
)
from syntavra_runtime.release_identity import CHANNEL, VERSION


class ProductSurfaceV001Tests(unittest.TestCase):
    def test_locked_identity_and_four_command_mental_model(self) -> None:
        self.assertEqual(VERSION, "0.0.1")
        self.assertEqual(CHANNEL, "pre-release")
        self.assertEqual([item.command for item in MENTAL_MODEL], ["setup", "status", "run", "prove"])
        self.assertEqual(MCP_PROFILES["minimal"].max_active_tools, 8)
        self.assertEqual(MCP_PROFILES["balanced"].max_active_tools, 36)
        self.assertEqual(MCP_PROFILES["audit"].exposed_tools, ("*",))

    def test_platform_adapter_registry_matches_host_matrix(self) -> None:
        value = PlatformAdapterRegistry.validate()
        self.assertTrue(value["ok"], value)
        self.assertGreaterEqual(value["adapters"], 18)
        self.assertGreaterEqual(value["mcp_capable"], 14)

    def test_tool_routing_fails_closed_for_unknown_and_unsafe_execution(self) -> None:
        read = ToolRoutingEnforcer.decide("repo.search")
        self.assertTrue(read.allowed)
        unknown = ToolRoutingEnforcer.decide("mystery.magic")
        self.assertFalse(unknown.allowed)
        execute = ToolRoutingEnforcer.decide("terminal.exec")
        self.assertFalse(execute.allowed)
        allowed = ToolRoutingEnforcer.decide(
            "terminal.exec",
            sandboxed=True,
            exact_evidence=True,
            explicit_user_authorization=True,
        )
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.category, "execute")
        self.assertGreaterEqual(len(allowed.receipt_hash), 32)

    @staticmethod
    def _receipt(index: int, arm: str) -> ProviderUsageReceipt:
        workloads = ("coding-agent", "repository-task", "swe-bench")
        baseline = arm == "baseline"
        return ProviderUsageReceipt(
            receipt_id=f"receipt-{index}-{arm}",
            provider="openai",
            model="test-model",
            request_id=f"request-{index}-{arm}",
            session_id=f"session-{index}",
            repository_hash=f"repository-{index % 5:02d}-0123456789abcdef",
            integration_id="codex",
            observed_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).isoformat(),
            wall_time_ms=1000.0 if baseline else 900.0,
            input_tokens=1000 if baseline else 650,
            cached_input_tokens=0 if baseline else 50,
            output_tokens=200,
            cost_usd=0.020 if baseline else 0.014,
            quality_score=0.90 if baseline else 0.91,
            success=True,
            synthetic=False,
            raw_usage_hash=("a" if baseline else "b") * 64,
            workload=workloads[index % len(workloads)],
            arm=arm,
            task_id=f"task-{index % 10:02d}",
            repetition=index + 1,
        )

    def test_measured_benchmark_gate_requires_and_accepts_real_paired_receipts(self) -> None:
        rows = []
        for index in range(30):
            rows.append(self._receipt(index, "baseline"))
            rows.append(self._receipt(index, "syntavra"))
        validation = ReceiptValidator.evaluate(rows)
        self.assertTrue(validation["ok"], validation)
        result = MeasuredBenchmarkGate.evaluate(rows)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["metrics"]["pairs"], 30)
        self.assertLess(result["metrics"]["mean_token_ratio"], 1.0)
        self.assertLess(result["metrics"]["mean_wall_time_ratio"], 1.0)
        self.assertGreaterEqual(result["metrics"]["mean_quality_delta"], 0.0)

    def test_synthetic_receipts_never_open_external_proof(self) -> None:
        row = self._receipt(0, "syntavra")
        synthetic = ProviderUsageReceipt(**{**row.__dict__, "synthetic": True})
        result = MeasuredBenchmarkGate.evaluate([synthetic])
        self.assertFalse(result["ok"])
        self.assertEqual(result["external_superiority"], "EXTERNAL_SUPERIORITY_NOT_PROVEN")

    def test_setup_bundle_and_content_free_session_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".syntavra" / "pre-release"
            bundle = ProductSurface.setup_bundle(root, state, "minimal")
            self.assertTrue(bundle["ok"])
            self.assertTrue((state / "product.json").is_file())
            analytics = SessionAnalyticsStore(state / "analytics" / "events.jsonl")
            analytics.record({
                "session_id": "session-1",
                "repository_hash": "repo-hash",
                "input_tokens": 100,
                "cached_input_tokens": 25,
                "output_tokens": 20,
                "wall_time_ms": 500,
                "cost_usd": 0.01,
                "continuity_restored": True,
                "prompt": "must not be stored",
                "response": "must not be stored",
            })
            raw = analytics.path.read_text(encoding="utf-8")
            self.assertNotIn("must not be stored", raw)
            report = analytics.report()
            self.assertEqual(report["sessions"], 1)
            self.assertEqual(report["usage"]["billable_input_tokens"], 75)
            self.assertEqual(report["continuity"]["restores"], 1)


if __name__ == "__main__":
    unittest.main()
