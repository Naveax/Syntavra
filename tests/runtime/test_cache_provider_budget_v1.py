from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.cache_provider_budget import (
    BudgetExhaustedError,
    CacheProviderBudgetEngine,
    ProviderBudgetPolicy,
)
from syntavra_runtime.prompt_cache_optimizer import PromptCacheOptimizer
from syntavra_runtime.provider_account_pool import ProviderAccountPool


def _models() -> list[dict[str, object]]:
    return [
        {
            "provider": "openai",
            "model": "reasoner",
            "quality": 0.95,
            "max_complexity": "reasoning",
            "context_window": 200_000,
            "input_cost_per_million": 12.0,
            "output_cost_per_million": 30.0,
            "cache_write_multiplier": 1.0,
            "cache_read_multiplier": 0.1,
        },
        {
            "provider": "anthropic",
            "model": "economy",
            "quality": 0.82,
            "max_complexity": "reasoning",
            "context_window": 200_000,
            "input_cost_per_million": 1.0,
            "output_cost_per_million": 2.0,
            "cache_write_multiplier": 1.25,
            "cache_read_multiplier": 0.1,
        },
        {
            "provider": "openrouter",
            "model": "compatible",
            "quality": 0.8,
            "max_complexity": "reasoning",
            "context_window": 200_000,
            "input_cost_per_million": 0.5,
            "output_cost_per_million": 1.0,
        },
    ]


def _messages(*, stable: str = "stable policy", volatile: str = "task") -> list[dict[str, object]]:
    return [
        {"role": "system", "content": stable * 800},
        {"role": "user", "content": volatile},
    ]


class CacheProviderBudgetV1Tests(unittest.TestCase):
    def _engine(self, root: Path) -> tuple[CacheProviderBudgetEngine, ProviderAccountPool]:
        pool = ProviderAccountPool(root / "accounts.sqlite3")
        pool.register(
            "openai",
            "primary",
            credential_ref="env:OPENAI_API_KEY",
            priority=10,
            quota_remaining=1.0,
        )
        pool.register(
            "anthropic",
            "economy",
            credential_ref="env:ANTHROPIC_API_KEY",
            priority=5,
            quota_remaining=1.0,
        )
        pool.register(
            "openrouter",
            "compatible",
            credential_ref="env:OPENROUTER_API_KEY",
            priority=1,
            quota_remaining=1.0,
        )
        return CacheProviderBudgetEngine(
            account_pool=pool,
            cache_optimizer=PromptCacheOptimizer(root),
        ), pool

    def test_budget_filters_expensive_provider_and_preserves_roi(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            decision = engine.plan(
                _messages(),
                task="security architecture root cause",
                model_rows=_models(),
                output_tokens_estimate=1200,
                policy=ProviderBudgetPolicy(
                    max_expected_cost_usd=0.08,
                    min_quality=0.8,
                    expected_requests=8,
                    require_prompt_cache=True,
                ),
                now=1000,
            )
        self.assertEqual(decision.provider, "anthropic")
        self.assertLessEqual(decision.expected_cost_usd, 0.08)
        self.assertGreater(decision.expected_savings_usd, 0)
        self.assertGreater(decision.savings_ratio, 0)
        self.assertTrue(any(row.provider == "openai" for row in decision.rejected))

    def test_explicit_prompt_cache_requirement_rejects_compatible_provider(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            decision = engine.plan(
                _messages(),
                task="simple lookup",
                model_rows=_models(),
                policy=ProviderBudgetPolicy(require_explicit_prompt_cache=True),
                now=1000,
            )
        self.assertIn(decision.provider, {"openai", "anthropic"})
        rejected = [row for row in decision.rejected if row.provider == "openrouter"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("explicit-prompt-cache-required", rejected[0].reasons)

    def test_budget_exhaustion_fails_closed_with_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            with self.assertRaises(BudgetExhaustedError) as caught:
                engine.plan(
                    _messages(),
                    task="security architecture root cause",
                    model_rows=_models(),
                    output_tokens_estimate=50_000,
                    policy=ProviderBudgetPolicy(max_expected_cost_usd=0.000001),
                    now=1000,
                )
        receipt = caught.exception.receipt
        self.assertEqual(receipt["decision"], "ABSTAIN")
        self.assertTrue(receipt["receipt_hash"])
        self.assertTrue(any("expected-cost-exceeds-budget" in row["reasons"] for row in receipt["rejected"]))

    def test_volatile_only_change_does_not_bust_stable_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            rows = _models()[:2]
            first = engine.plan(_messages(volatile="one"), task="simple", model_rows=rows, now=1000)
            second = engine.plan(
                _messages(volatile="two"),
                task="simple",
                model_rows=rows,
                previous=first,
                now=1001,
            )
        self.assertEqual(first.provider, second.provider)
        self.assertEqual(first.model, second.model)
        self.assertEqual(first.stable_prefix_hash, second.stable_prefix_hash)
        self.assertEqual(second.cache_bust_reasons, ("none",))

    def test_stable_prefix_change_is_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            rows = _models()[:2]
            first = engine.plan(_messages(stable="policy-a"), task="simple", model_rows=rows, now=1000)
            second = engine.plan(
                _messages(stable="policy-b"),
                task="simple",
                model_rows=rows,
                previous=first,
                now=1001,
            )
        self.assertIn("stable-prefix-changed", second.cache_bust_reasons)

    def test_expired_previous_cache_is_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            rows = _models()[:2]
            first = engine.plan(_messages(), task="simple", model_rows=rows, now=1000)
            second = engine.plan(
                _messages(),
                task="simple",
                model_rows=rows,
                previous=first,
                now=first.cache_expires_at + 1,
            )
        self.assertIn("previous-cache-expired", second.cache_bust_reasons)

    def test_account_circuit_state_is_reused_for_deterministic_failover(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool = ProviderAccountPool(root / "accounts.sqlite3")
            pool.register("openai", "primary", credential_ref="env:OPENAI_PRIMARY", priority=20)
            pool.register("openai", "backup", credential_ref="env:OPENAI_BACKUP", priority=1)
            engine = CacheProviderBudgetEngine(account_pool=pool, cache_optimizer=PromptCacheOptimizer(root))
            rows = [_models()[0]]
            first = engine.plan(_messages(), task="simple", model_rows=rows, now=1000)
            self.assertEqual(first.account, "primary")
            for offset in range(3):
                pool.record_result("openai", "primary", success=False, now=1000 + offset)
            second = engine.plan(_messages(), task="simple", model_rows=rows, now=1003)
        self.assertEqual(second.account, "backup")

    def test_fallback_order_and_receipt_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            policy = ProviderBudgetPolicy(expected_requests=3)
            first = engine.plan(_messages(), task="simple", model_rows=_models(), policy=policy, now=1000)
            second = engine.plan(_messages(), task="simple", model_rows=_models(), policy=policy, now=1000)
        self.assertEqual(first.fallbacks, second.fallbacks)
        self.assertEqual(first.receipt_hash, second.receipt_hash)

    def test_decision_does_not_expose_credential_references(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine, _ = self._engine(Path(td))
            decision = engine.plan(_messages(), task="simple", model_rows=_models(), now=1000)
        rendered = json.dumps(decision.__dict__, default=str)
        self.assertNotIn("OPENAI_API_KEY", rendered)
        self.assertNotIn("ANTHROPIC_API_KEY", rendered)
        self.assertNotIn("credential_ref", rendered)

    def test_context_window_and_quota_reserve_are_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pool = ProviderAccountPool(root / "accounts.sqlite3")
            pool.register("openai", "low-quota", credential_ref="env:OPENAI_LOW", quota_remaining=0.1)
            engine = CacheProviderBudgetEngine(account_pool=pool, cache_optimizer=PromptCacheOptimizer(root))
            tiny = [{**_models()[0], "context_window": 100}]
            with self.assertRaises(BudgetExhaustedError):
                engine.plan(
                    _messages(),
                    task="simple",
                    model_rows=tiny,
                    policy=ProviderBudgetPolicy(min_quota_remaining=0.5),
                    now=1000,
                )

    def test_runtime_reuses_existing_authorities_and_adds_no_store(self) -> None:
        status = CacheProviderBudgetEngine.status()
        self.assertTrue(status["provider_budget_engine"])
        self.assertTrue(status["cache_roi"])
        self.assertTrue(status["cache_bust_attribution"])
        self.assertTrue(status["provider_capability_negotiation"])
        self.assertTrue(status["deterministic_fallback_policy"])
        self.assertFalse(status["parallel_persistent_store"])
        self.assertFalse(status["credential_ownership"])
        self.assertFalse(status["public_cli_route"])


if __name__ == "__main__":
    unittest.main()
