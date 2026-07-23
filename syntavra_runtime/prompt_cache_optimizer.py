from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .util import atomic_write_json, canonical_json, read_json, sha256_bytes


@dataclass(frozen=True)
class CacheSegment:
    role: str
    stable: bool
    bytes: int
    tokens_estimate: int
    content_hash: str
    reason: str


@dataclass(frozen=True)
class CachePlan:
    provider: str
    model: str
    stable_prefix_hash: str
    stable_messages: int
    volatile_messages: int
    cacheable_tokens: int
    volatile_tokens: int
    ttl_seconds: int
    expires_at: float
    refresh_after: float
    reordered: bool
    segments: tuple[CacheSegment, ...]


_PROVIDER_TTLS = {
    "anthropic": 300,
    "openai": 600,
    "google": 3600,
    "gemini": 3600,
    "groq": 600,
    "openrouter": 600,
}
_VOLATILE_KEYS = {"timestamp", "request_id", "trace_id", "nonce", "usage", "cost", "latency_ms"}


class PromptCacheOptimizer:
    def __init__(self, state_root: Path):
        self.state_root = Path(state_root)
        self.path = self.state_root / "cache" / "plans.json"

    @staticmethod
    def _stable_message(message: Mapping[str, Any]) -> bool:
        role = str(message.get("role") or "").casefold()
        if role in {"system", "developer"}:
            return True
        if role == "tool" and message.get("cache_control") == "stable":
            return True
        return bool(message.get("stable") or message.get("cacheable"))

    @staticmethod
    def _clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): PromptCacheOptimizer._clean(child) for key, child in sorted(value.items(), key=lambda item: str(item[0])) if str(key) not in _VOLATILE_KEYS and not str(key).startswith("_")}
        if isinstance(value, list):
            return [PromptCacheOptimizer._clean(item) for item in value]
        return value

    def plan(self, messages: Sequence[Mapping[str, Any]], *, provider: str, model: str, ttl_seconds: int | None = None, reorder: bool = True, now: float | None = None) -> CachePlan:
        now = time.time() if now is None else float(now)
        provider_name = provider.strip().casefold() or "unknown"
        ttl = int(ttl_seconds or _PROVIDER_TTLS.get(provider_name, 600))
        stable_rows = [dict(row) for row in messages if self._stable_message(row)]
        volatile_rows = [dict(row) for row in messages if not self._stable_message(row)]
        ordered = [*stable_rows, *volatile_rows] if reorder else [dict(row) for row in messages]
        stable_prefix = [self._clean(row) for row in ordered[:len(stable_rows)]]
        stable_hash = sha256_bytes(canonical_json(stable_prefix))
        segments: list[CacheSegment] = []
        for row in ordered:
            clean = self._clean(row)
            raw = canonical_json(clean)
            stable = self._stable_message(row)
            segments.append(CacheSegment(str(row.get("role") or "unknown"), stable, len(raw), max(1, len(raw) // 4), sha256_bytes(raw), "stable-prefix" if stable else "volatile-tail"))
        plan = CachePlan(
            provider_name, model, stable_hash, len(stable_rows), len(volatile_rows),
            sum(item.tokens_estimate for item in segments if item.stable),
            sum(item.tokens_estimate for item in segments if not item.stable),
            ttl, now + ttl, now + ttl * 0.75,
            reorder and ordered != list(messages), tuple(segments),
        )
        self._save(plan)
        return plan

    def _save(self, plan: CachePlan) -> None:
        current = read_json(self.path, {}) or {}
        plans = dict(current.get("plans") or {})
        plans[f"{plan.provider}:{plan.model}:{plan.stable_prefix_hash}"] = asdict(plan)
        atomic_write_json(self.path, {"plans": plans, "updated_at": time.time()})

    def health(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        current = read_json(self.path, {}) or {}
        rows = list((current.get("plans") or {}).values())
        expiring = [row for row in rows if float(row.get("refresh_after", 0)) <= now < float(row.get("expires_at", 0))]
        expired = [row for row in rows if float(row.get("expires_at", 0)) <= now]
        active = [row for row in rows if now < float(row.get("refresh_after", 0))]
        return {"plans": len(rows), "active": len(active), "refresh_due": len(expiring), "expired": len(expired), "cacheable_tokens": sum(int(row.get("cacheable_tokens", 0)) for row in rows)}

    @staticmethod
    def amortization(*, cache_write_tokens: int, cache_read_tokens: int, uncached_input_tokens: int, requests: int, write_multiplier: float = 1.25, read_multiplier: float = 0.1) -> dict[str, float]:
        requests = max(1, int(requests))
        baseline = float(uncached_input_tokens * requests)
        optimized = float(cache_write_tokens * write_multiplier + cache_read_tokens * read_multiplier * max(0, requests - 1))
        return {"baseline_equivalent": baseline, "optimized_equivalent": optimized, "saved_equivalent": max(0.0, baseline - optimized), "savings_ratio": max(0.0, (baseline - optimized) / baseline) if baseline else 0.0, "break_even_requests": (cache_write_tokens * write_multiplier) / max(1.0, uncached_input_tokens - cache_read_tokens * read_multiplier)}
