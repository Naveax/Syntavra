from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.provider_proxy import ProviderProxyRuntime, ProxyConfig
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger


class ProviderNativeToolSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = EvidenceStore(self.root / "evidence", project_id="tool-search-test")
        self.ledger = UsageReceiptLedger(self.root / "usage.sqlite3", signing_key=b"test-signing-key")
        self.gateway = ProviderGateway(
            self.root / "gateway.sqlite3",
            evidence=self.evidence,
            usage_ledger=self.ledger,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _runtime(self, provider: str, **values) -> ProviderProxyRuntime:
        config = ProxyConfig(
            provider=provider,
            upstream_base="https://provider.invalid/v1",
            cache_policy="off",
            **values,
        )
        return ProviderProxyRuntime(
            config,
            gateway=self.gateway,
            insight_path=self.root / f"insights-{provider}.sqlite3",
        )

    def test_capability_matrix_is_provider_and_model_specific(self) -> None:
        anthropic = ProviderProxyRuntime.tool_search_capability("anthropic", "claude-sonnet-4-5-20250929")
        self.assertTrue(anthropic["supported"])
        self.assertEqual(anthropic["native_mode"], "anthropic-bm25")

        openai = ProviderProxyRuntime.tool_search_capability("openai", "gpt-5.4")
        self.assertTrue(openai["supported"])
        self.assertEqual(openai["native_mode"], "openai-hosted")

        self.assertFalse(ProviderProxyRuntime.tool_search_capability("openai", "gpt-5.4-nano")["supported"])
        self.assertFalse(ProviderProxyRuntime.tool_search_capability("openai", "gpt-5.5-pro")["supported"])
        self.assertFalse(ProviderProxyRuntime.tool_search_capability("openai", "chat-latest")["supported"])
        self.assertFalse(ProviderProxyRuntime.tool_search_capability("openai-compatible", "gpt-5.4")["supported"])

    def test_benchmark_admission_is_required_before_request_mutation(self) -> None:
        runtime = self._runtime(
            "anthropic",
            native_tool_search="auto",
            native_tool_search_benchmark_admitted=False,
        )
        payload = {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [{"role": "user", "content": "find a tool"}],
            "tools": [{"name": "lookup_user", "description": "Lookup user", "input_schema": {"type": "object"}}],
        }
        plan = runtime._prepare(payload)
        self.assertEqual(plan.prepared_request["tools"], payload["tools"])
        self.assertIn("native-tool-search-not-benchmark-admitted", plan.reasons)

    def test_anthropic_bm25_deferred_loading_and_cache_conflict_fail_closed(self) -> None:
        runtime = self._runtime(
            "anthropic",
            native_tool_search="auto",
            native_tool_search_benchmark_admitted=True,
        )
        payload = {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [{"role": "user", "content": "find the account tool"}],
            "tools": [
                {"name": "lookup_user", "description": "Lookup user", "input_schema": {"type": "object"}},
                {
                    "name": "cached_tool",
                    "description": "Must remain upfront",
                    "input_schema": {"type": "object"},
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "name": "explicit_upfront",
                    "description": "Caller requires upfront loading",
                    "input_schema": {"type": "object"},
                    "defer_loading": False,
                },
            ],
        }
        plan = runtime._prepare(payload)
        tools = plan.prepared_request["tools"]
        self.assertEqual(tools[0]["type"], "tool_search_tool_bm25_20251119")
        self.assertEqual(tools[0]["name"], "tool_search_tool_bm25")
        by_name = {tool.get("name"): tool for tool in tools if isinstance(tool, dict) and tool.get("name")}
        self.assertTrue(by_name["lookup_user"]["defer_loading"])
        self.assertNotIn("defer_loading", by_name["cached_tool"])
        self.assertFalse(by_name["explicit_upfront"]["defer_loading"])
        self.assertIn("native-tool-search-anthropic-bm25", plan.reasons)
        self.assertIn("native-tool-search-cache-control-conflict-preserved-upfront", plan.reasons)

        exact_request = json.loads(self.evidence.get(plan.request_handle))
        self.assertEqual(exact_request["tools"][0]["type"], "tool_search_tool_bm25_20251119")
        self.assertTrue(exact_request["tools"][1]["defer_loading"])

    def test_anthropic_regex_variant_is_explicit_and_deterministic(self) -> None:
        runtime = self._runtime(
            "anthropic",
            native_tool_search="anthropic-regex",
            native_tool_search_benchmark_admitted=True,
        )
        payload = {
            "model": "claude-opus-4-6",
            "messages": [{"role": "user", "content": "find tool"}],
            "tools": [{"name": "search_repo", "description": "Search repo", "input_schema": {"type": "object"}}],
        }
        plan = runtime._prepare(payload)
        self.assertEqual(plan.prepared_request["tools"][0]["type"], "tool_search_tool_regex_20251119")
        self.assertEqual(plan.prepared_request["tools"][0]["name"], "tool_search_tool_regex")
        self.assertIn("native-tool-search-anthropic-regex", plan.reasons)

    def test_openai_responses_hosted_search_defer_functions_only(self) -> None:
        runtime = self._runtime(
            "openai",
            native_tool_search="auto",
            native_tool_search_benchmark_admitted=True,
        )
        payload = {
            "model": "gpt-5.4",
            "input": [{"role": "user", "content": "find a tool"}],
            "tools": [
                {"type": "function", "name": "lookup_user", "description": "Lookup user", "parameters": {"type": "object"}},
                {"type": "web_search"},
            ],
            "temperature": 0,
        }
        plan = runtime._prepare(payload)
        tools = plan.prepared_request["tools"]
        functions = [tool for tool in tools if tool.get("type") == "function"]
        self.assertEqual(len(functions), 1)
        self.assertTrue(functions[0]["defer_loading"])
        self.assertEqual(sum(1 for tool in tools if tool.get("type") == "tool_search"), 1)
        web = next(tool for tool in tools if tool.get("type") == "web_search")
        self.assertNotIn("defer_loading", web)
        self.assertIn("native-tool-search-openai-hosted", plan.reasons)

    def test_openai_chat_and_unsupported_models_preserve_portable_fallback(self) -> None:
        runtime = self._runtime(
            "openai",
            native_tool_search="auto",
            native_tool_search_benchmark_admitted=True,
        )
        chat = {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "find a tool"}],
            "tools": [{"type": "function", "function": {"name": "lookup_user", "parameters": {"type": "object"}}}],
        }
        chat_plan = runtime._prepare(chat)
        self.assertEqual(chat_plan.prepared_request["tools"], chat["tools"])
        self.assertIn("native-tool-search-openai-responses-required", chat_plan.reasons)

        nano = {
            "model": "gpt-5.4-nano",
            "input": [{"role": "user", "content": "find a tool"}],
            "tools": [{"type": "function", "name": "lookup_user", "parameters": {"type": "object"}}],
        }
        nano_plan = runtime._prepare(nano)
        self.assertEqual(nano_plan.prepared_request["tools"], nano["tools"])
        self.assertIn("native-tool-search-model-unsupported", nano_plan.reasons)


if __name__ == "__main__":
    unittest.main()
