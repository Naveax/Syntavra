from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from syntavra_runtime.model_gateway import (
    AnthropicGateway,
    ContextEditingConfig,
    GatewayConfig,
    provider_context_editing_capability,
)
from syntavra_runtime.provider_call_observation import ProviderCallObservationLedger


class _StubAnthropicGateway(AnthropicGateway):
    def __init__(self, config: GatewayConfig) -> None:
        super().__init__(config)
        self.last_payload: Mapping[str, Any] | None = None
        self.last_headers: Mapping[str, str] | None = None

    def _api_key(self) -> str:
        return "test-key"

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        del url
        self.last_payload = dict(payload)
        self.last_headers = dict(headers)
        return {
            "id": "msg_test",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1200, "output_tokens": 80},
            "context_management": {
                "applied_edits": [
                    {
                        "type": "clear_thinking_20251015",
                        "cleared_thinking_turns": 2,
                        "cleared_input_tokens": 1500,
                    },
                    {
                        "type": "clear_tool_uses_20250919",
                        "cleared_tool_uses": 8,
                        "cleared_input_tokens": 5000,
                    },
                ]
            },
        }


class ProviderNativeContextEditingTests(unittest.TestCase):
    def test_capabilities_fail_closed_by_transport(self) -> None:
        anthropic = provider_context_editing_capability(
            GatewayConfig(provider="anthropic", model="claude-test")
        )
        self.assertTrue(anthropic.supported)
        self.assertTrue(anthropic.directly_admissible)
        self.assertTrue(anthropic.reports_cleared_input_tokens)

        openai = provider_context_editing_capability(
            GatewayConfig(provider="openai", model="gpt-test", api_mode="responses")
        )
        self.assertTrue(openai.supported)
        self.assertFalse(openai.directly_admissible)
        self.assertEqual(openai.native_mode, "openai-responses-compaction")

        portable = provider_context_editing_capability(
            GatewayConfig(provider="local", model="local-test")
        )
        self.assertFalse(portable.supported)
        self.assertEqual(portable.native_mode, "portable-syntavra-fallback")

    def test_anthropic_native_editing_requires_benchmark_admission(self) -> None:
        gateway = _StubAnthropicGateway(
            GatewayConfig(
                provider="anthropic",
                model="claude-test",
                context_editing=ContextEditingConfig(
                    enabled=True,
                    benchmark_admitted=False,
                ),
            )
        )
        gateway.complete([{"role": "user", "content": "task"}])
        assert gateway.last_payload is not None
        assert gateway.last_headers is not None
        self.assertNotIn("context_management", gateway.last_payload)
        self.assertNotIn("anthropic-beta", gateway.last_headers)

    def test_anthropic_payload_and_cleared_token_diagnostics(self) -> None:
        gateway = _StubAnthropicGateway(
            GatewayConfig(
                provider="anthropic",
                model="claude-test",
                context_editing=ContextEditingConfig(
                    enabled=True,
                    benchmark_admitted=True,
                    clear_tool_uses=True,
                    clear_thinking=True,
                    trigger_input_tokens=30_000,
                    keep_tool_uses=5,
                    clear_at_least_tokens=5_000,
                    keep_thinking_turns=2,
                    exclude_tools=("web_search",),
                ),
            )
        )
        result = gateway.complete([{"role": "user", "content": "task"}])
        assert gateway.last_payload is not None
        assert gateway.last_headers is not None
        self.assertEqual(
            gateway.last_headers["anthropic-beta"],
            "context-management-2025-06-27",
        )
        edits = gateway.last_payload["context_management"]["edits"]
        self.assertEqual(edits[0]["type"], "clear_thinking_20251015")
        self.assertEqual(edits[0]["keep"], {"type": "thinking_turns", "value": 2})
        self.assertEqual(edits[1]["type"], "clear_tool_uses_20250919")
        self.assertEqual(edits[1]["trigger"], {"type": "input_tokens", "value": 30_000})
        self.assertEqual(edits[1]["keep"], {"type": "tool_uses", "value": 5})
        self.assertEqual(edits[1]["clear_at_least"], {"type": "input_tokens", "value": 5_000})
        self.assertEqual(edits[1]["exclude_tools"], ["web_search"])
        self.assertEqual(result.diagnostics["context_editing"]["cleared_input_tokens"], 6500)
        self.assertEqual(result.usage["input_tokens"], 1200)
        self.assertEqual(result.usage["output_tokens"], 80)

    def test_observation_receipts_cleared_tokens_without_billing_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ProviderCallObservationLedger(Path(tmp) / "provider-observations.db")
            response = {
                "id": "msg_test",
                "context_management": {
                    "applied_edits": [
                        {"type": "clear_tool_uses_20250919", "cleared_input_tokens": 5000},
                        {"type": "clear_thinking_20251015", "cleared_input_tokens": 1500},
                    ]
                },
            }
            row = ledger.record_call(
                task_id="task-1",
                arm_id="candidate",
                repetition=1,
                round_number=1,
                provider="anthropic",
                model="claude-test",
                usage={"input_tokens": 1200, "output_tokens": 80},
                response_id="msg_test",
                response=response,
                locally_counted_input_tokens=1300,
                counting_method="test-tokenizer",
                context_hash="c" * 64,
                elapsed_ms=25,
            )
            self.assertTrue(row.provider_observed)
            self.assertEqual(row.cleared_input_tokens, 6500)
            self.assertEqual(row.provider_total_tokens, 1280)
            summary = ledger.summary(task_id="task-1", arm_id="candidate")
            self.assertEqual(summary["schema_version"], 2)
            self.assertEqual(summary["cleared_input_tokens"], 6500)
            self.assertEqual(summary["fresh_input_tokens"] + summary["output_tokens"], 1280)


if __name__ == "__main__":
    unittest.main()
