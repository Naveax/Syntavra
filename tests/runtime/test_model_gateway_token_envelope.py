from __future__ import annotations

import unittest
from typing import Any, Mapping

from syntavra_runtime.model_gateway import (
    GatewayConfig,
    GatewayError,
    OpenAICompatibleGateway,
)
from syntavra_runtime.provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelopeCompiler,
)


class _StubGateway(OpenAICompatibleGateway):
    def __init__(self, config: GatewayConfig) -> None:
        super().__init__(config)
        self.last_payload: Mapping[str, Any] | None = None

    def _api_key(self) -> str:
        return ""

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        del url, headers
        self.last_payload = dict(payload)
        return {
            "choices": [
                {
                    "message": {"content": "{\"action\":\"search\"}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": self.config.prepared_input_tokens,
                "completion_tokens": 32,
            },
        }


class ModelGatewayTokenEnvelopeTests(unittest.TestCase):
    @staticmethod
    def _envelope():
        return ProviderTokenEnvelopeCompiler().compile_from_leases(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            input_leases=(
                NecessityLease("system", "system_instruction", 800, "request:system", True),
                NecessityLease("user", "user_instruction", 1_200, "request:user", True),
            ),
            output_leases=(
                NecessityLease("answer", "requested_output", 400, "request:answer", True),
            ),
        )

    def test_gateway_applies_output_budget_before_dispatch(self) -> None:
        envelope = self._envelope()
        gateway = _StubGateway(
            GatewayConfig(
                provider="openai-compatible",
                model="test",
                endpoint="http://127.0.0.1:1/v1",
                api_mode="chat",
                max_output_tokens=8192,
                token_envelope=envelope,
                prepared_input_tokens=6000,
            )
        )
        result = gateway.complete([{"role": "user", "content": "task"}])
        self.assertEqual(result.usage["input_tokens"], 6000)
        self.assertIsNotNone(gateway.last_payload)
        assert gateway.last_payload is not None
        self.assertEqual(gateway.last_payload["max_tokens"], 2000)

    def test_gateway_rejects_prepared_input_over_budget(self) -> None:
        envelope = self._envelope()
        gateway = _StubGateway(
            GatewayConfig(
                provider="openai-compatible",
                model="test",
                endpoint="http://127.0.0.1:1/v1",
                api_mode="chat",
                token_envelope=envelope,
                prepared_input_tokens=6001,
            )
        )
        with self.assertRaisesRegex(GatewayError, "exceeds token envelope"):
            gateway.complete([{"role": "user", "content": "task"}])
        self.assertIsNone(gateway.last_payload)

    def test_gateway_requires_tokenizer_observed_input_count(self) -> None:
        envelope = self._envelope()
        gateway = _StubGateway(
            GatewayConfig(
                provider="openai-compatible",
                model="test",
                endpoint="http://127.0.0.1:1/v1",
                api_mode="chat",
                token_envelope=envelope,
                prepared_input_tokens=0,
            )
        )
        with self.assertRaisesRegex(GatewayError, "tokenizer-observed"):
            gateway.complete([{"role": "user", "content": "task"}])

    def test_gateway_rejects_unadmitted_envelope_at_construction(self) -> None:
        envelope = ProviderTokenEnvelopeCompiler().compile(
            original_input_tokens=100_000,
            original_output_budget_tokens=10_000,
            mandatory_input_tokens=2_000,
        )
        self.assertFalse(envelope.provider_call_admissible)
        with self.assertRaisesRegex(ValueError, "does not admit"):
            _StubGateway(
                GatewayConfig(
                    provider="openai-compatible",
                    model="test",
                    endpoint="http://127.0.0.1:1/v1",
                    api_mode="chat",
                    token_envelope=envelope,
                    prepared_input_tokens=6000,
                )
            )

    def test_gateway_without_envelope_preserves_legacy_limit(self) -> None:
        gateway = _StubGateway(
            GatewayConfig(
                provider="openai-compatible",
                model="test",
                endpoint="http://127.0.0.1:1/v1",
                api_mode="chat",
                max_output_tokens=8192,
            )
        )
        gateway.complete([{"role": "user", "content": "task"}])
        assert gateway.last_payload is not None
        self.assertEqual(gateway.last_payload["max_tokens"], 8192)


if __name__ == "__main__":
    unittest.main()
