from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_call_observation import ProviderCallObservationLedger
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger


class OpenAIPromptCacheModernizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = EvidenceStore(self.root / "evidence", project_id="openai-cache-modernization")
        self.usage = UsageReceiptLedger(self.root / "usage.sqlite3", signing_key=b"cache-modernization-test")
        self.gateway = ProviderGateway(
            self.root / "gateway.sqlite3",
            evidence=self.evidence,
            usage_ledger=self.usage,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _responses_request(model: str = "gpt-5.6") -> dict:
        return {
            "model": model,
            "input": [
                {"role": "developer", "content": "stable repository policy"},
                {"role": "user", "content": "fix the failing test"},
            ],
            "temperature": 0,
        }

    def test_gpt56_responses_uses_modern_cache_options_without_retention(self) -> None:
        plan = self.gateway.prepare(
            "responses",
            self._responses_request(),
            prompt_cache_ttl_seconds=86_400,
        )
        self.assertEqual(plan.prompt_cache_mode, "provider-modern-cache-options")
        self.assertEqual(plan.prepared_request["prompt_cache_options"], {"mode": "implicit", "ttl": "30m"})
        self.assertNotIn("prompt_cache_retention", plan.prepared_request)
        self.assertIn("prompt-cache-profile:openai-responses-gpt56-v1", plan.reasons)
        self.assertIn("openai-modern-cache-ttl-normalized-to-30m", plan.reasons)

    def test_earlier_openai_model_keeps_legacy_retention_lane(self) -> None:
        request = {
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "stable policy"},
                {"role": "user", "content": "task"},
            ],
            "temperature": 0,
        }
        plan = self.gateway.prepare("openai", request, prompt_cache_ttl_seconds=86_400)
        self.assertEqual(plan.prompt_cache_mode, "provider-explicit-key")
        self.assertEqual(plan.prepared_request["prompt_cache_retention"], "24h")
        self.assertNotIn("prompt_cache_options", plan.prepared_request)
        self.assertIn("prompt-cache-profile:openai-chat-legacy-v1", plan.reasons)

    def test_azure_openai_does_not_silently_inherit_direct_openai_modern_profile(self) -> None:
        plan = self.gateway.prepare(
            "azure-openai",
            self._responses_request(),
            prompt_cache_ttl_seconds=86_400,
        )
        self.assertEqual(plan.prompt_cache_mode, "provider-explicit-key")
        self.assertIn("prompt_cache_retention", plan.prepared_request)
        self.assertNotIn("prompt_cache_options", plan.prepared_request)
        self.assertIn("prompt-cache-profile:openai-azure-legacy-v1", plan.reasons)

    def test_modern_and_legacy_adapter_profiles_cannot_share_cache_identity(self) -> None:
        request = self._responses_request()
        modern = self.gateway.prepare("responses", request)
        compatible = self.gateway.prepare("azure-openai", request)
        self.assertNotEqual(modern.cache_key, compatible.cache_key)
        self.assertNotEqual(modern.stable_prefix_hash, compatible.stable_prefix_hash)

    def test_mixed_cache_control_families_fail_closed(self) -> None:
        modern = self._responses_request()
        modern["prompt_cache_retention"] = "24h"
        with self.assertRaisesRegex(ValueError, "prompt_cache_retention"):
            self.gateway.prepare("responses", modern)

        legacy = {
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": "task"}],
            "prompt_cache_options": {"ttl": "30m"},
        }
        with self.assertRaisesRegex(ValueError, "prompt_cache_options"):
            self.gateway.prepare("openai", legacy)

    def test_gateway_capture_exposes_cache_write_diagnostic_without_recounting_input(self) -> None:
        plan = self.gateway.prepare("responses", self._responses_request())
        capture = self.gateway.capture(
            plan,
            {
                "id": "resp-cache-write",
                "output_text": "ok",
                "usage": {
                    "input_tokens": 15_000,
                    "input_tokens_details": {"cached_tokens": 12_000, "cache_write_tokens": 3_000},
                    "output_tokens": 100,
                },
            },
            store_replay=False,
        )
        self.assertEqual(capture.normalized_usage["fresh_input_tokens"], 3_000)
        self.assertEqual(capture.normalized_usage["cached_input_tokens"], 12_000)
        self.assertEqual(capture.normalized_usage["cache_write_tokens"], 3_000)
        self.assertEqual(
            capture.normalized_usage["fresh_input_tokens"]
            + capture.normalized_usage["cached_input_tokens"]
            + capture.normalized_usage["output_tokens"],
            15_100,
        )

    def test_observation_ledger_persists_cache_write_tokens_as_non_additive_diagnostic(self) -> None:
        ledger = ProviderCallObservationLedger(self.root / "observations.sqlite3")
        row = ledger.record_call(
            task_id="cache-task",
            arm_id="candidate",
            repetition=1,
            round_number=1,
            provider="openai",
            model="gpt-5.6",
            usage={
                "input_tokens": 15_000,
                "input_tokens_details": {"cached_tokens": 12_000, "cache_write_tokens": 3_000},
                "output_tokens": 100,
            },
            response_id="resp-observation",
            response={"id": "resp-observation", "output_text": "ok"},
            locally_counted_input_tokens=15_000,
            counting_method="test",
            context_hash="c" * 64,
            elapsed_ms=12,
        )
        self.assertTrue(row.provider_observed)
        self.assertEqual(row.cache_write_tokens, 3_000)
        self.assertEqual(row.provider_total_tokens, 15_100)
        summary = ledger.summary(task_id="cache-task", arm_id="candidate")
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["cache_write_tokens"], 3_000)
        self.assertEqual(
            summary["fresh_input_tokens"]
            + summary["cached_input_tokens"]
            + summary["output_tokens"],
            15_100,
        )


if __name__ == "__main__":
    unittest.main()
