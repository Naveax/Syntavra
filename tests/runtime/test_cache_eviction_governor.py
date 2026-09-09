from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from syntavra_runtime.prompt_cache_optimizer import PromptCacheOptimizer


class CacheEvictionGovernorTests(unittest.TestCase):
    @staticmethod
    def _messages(label: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": f"stable-{label}"}]

    @staticmethod
    def _plans(state: Path) -> dict[str, object]:
        return json.loads((state / "cache" / "plans.json").read_text(encoding="utf-8"))["plans"]

    def test_reuse_probability_controls_capacity_eviction(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=2)
            high = cache.plan(self._messages("high"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.9, rebuild_cost_tokens=100)
            low = cache.plan(self._messages("low"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.1, rebuild_cost_tokens=100)
            middle = cache.plan(self._messages("middle"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)

            plans = self._plans(state)
            self.assertIn(cache._cache_key(high), plans)
            self.assertIn(cache._cache_key(middle), plans)
            self.assertNotIn(cache._cache_key(low), plans)
            receipt = cache.governance_receipts(limit=1)[0]
            self.assertEqual(receipt["decision"], "admit")
            self.assertEqual(receipt["reason"], "admitted-evicted-lower-value")
            self.assertEqual(receipt["evicted_keys"], [cache._cache_key(low)])

    def test_rebuild_cost_controls_capacity_eviction(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=2)
            cheap = cache.plan(self._messages("cheap"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=10)
            expensive = cache.plan(self._messages("expensive"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
            middle = cache.plan(self._messages("middle"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=50)

            plans = self._plans(state)
            self.assertNotIn(cache._cache_key(cheap), plans)
            self.assertIn(cache._cache_key(expensive), plans)
            self.assertIn(cache._cache_key(middle), plans)

    def test_ttl_controls_capacity_eviction(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=2)
            short = cache.plan(self._messages("short"), provider="openai", model="m", ttl_seconds=10, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
            long = cache.plan(self._messages("long"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
            middle = cache.plan(self._messages("middle"), provider="openai", model="m", ttl_seconds=50, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)

            plans = self._plans(state)
            self.assertNotIn(cache._cache_key(short), plans)
            self.assertIn(cache._cache_key(long), plans)
            self.assertIn(cache._cache_key(middle), plans)

    def test_expired_entries_are_reclaimed_and_receipted(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=8)
            stale = cache.plan(self._messages("stale"), provider="openai", model="m", ttl_seconds=10, now=1000)
            fresh = cache.plan(self._messages("fresh"), provider="openai", model="m", ttl_seconds=100, now=1011)

            plans = self._plans(state)
            self.assertNotIn(cache._cache_key(stale), plans)
            self.assertIn(cache._cache_key(fresh), plans)
            receipt = cache.governance_receipts(limit=1)[0]
            self.assertEqual(receipt["expired_keys"], [cache._cache_key(stale)])
            self.assertEqual(receipt["reason"], "admitted-after-expiry-reclamation")

    def test_maintenance_reclaims_without_new_admission(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=8)
            stale = cache.plan(self._messages("stale"), provider="anthropic", model="m", ttl_seconds=5, now=1000)
            result = cache.maintain(now=1006)
            self.assertTrue(result["changed"])
            self.assertEqual(result["expired_keys"], [cache._cache_key(stale)])
            self.assertEqual(self._plans(state), {})

    def test_equal_value_tie_break_is_cache_key_deterministic(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=1)
            first = cache.plan(self._messages("first"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
            second = cache.plan(self._messages("second"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
            first_key = cache._cache_key(first)
            second_key = cache._cache_key(second)
            plans = self._plans(state)
            self.assertEqual(set(plans), {max(first_key, second_key)})
            self.assertEqual(cache.governance_receipts(limit=1)[0]["evicted_keys"], [min(first_key, second_key)])

    def test_governance_receipt_is_machine_readable_and_bounded(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td)
            cache = PromptCacheOptimizer(state, max_entries=2)
            cache.GOVERNANCE_RECEIPT_LIMIT = 3
            for index in range(5):
                cache.plan(
                    self._messages(str(index)),
                    provider="openai",
                    model="m",
                    ttl_seconds=100,
                    now=1000 + index,
                    reuse_probability=0.5,
                    rebuild_cost_tokens=100,
                )
            receipts = cache.governance_receipts(limit=20)
            self.assertEqual(len(receipts), 3)
            required = {
                "schema_version", "decision", "reason", "cache_key",
                "reuse_probability", "ttl_seconds", "rebuild_cost_tokens",
                "value_score", "max_entries", "expired_keys", "evicted_keys",
                "retained_entries",
            }
            self.assertTrue(required <= set(receipts[-1]))

    def test_invalid_reuse_probability_fails_closed(self) -> None:
        with TemporaryDirectory() as td:
            cache = PromptCacheOptimizer(Path(td))
            with self.assertRaises(ValueError):
                cache.plan(self._messages("bad"), provider="openai", model="m", now=1000, reuse_probability=1.1)


if __name__ == "__main__":
    unittest.main()
