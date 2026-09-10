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
    cache_profile: str = "default"


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
    GOVERNANCE_SCHEMA_VERSION = 1
    GOVERNANCE_RECEIPT_LIMIT = 256

    def __init__(self, state_root: Path, *, max_entries: int = 128):
        self.state_root = Path(state_root)
        self.path = self.state_root / "cache" / "plans.json"
        self.governance_path = self.state_root / "cache" / "governance.json"
        self.max_entries = max(1, int(max_entries))

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

    @staticmethod
    def _cache_key(plan: CachePlan) -> str:
        return f"{plan.provider}:{plan.model}:{plan.cache_profile}:{plan.stable_prefix_hash}"

    @staticmethod
    def _validate_reuse_probability(value: float) -> float:
        probability = float(value)
        if not 0.0 <= probability <= 1.0:
            raise ValueError("reuse_probability must be between 0 and 1")
        return probability

    @staticmethod
    def _entry_value(row: Mapping[str, Any], meta: Mapping[str, Any], now: float) -> float:
        reuse_probability = max(0.0, min(1.0, float(meta.get("reuse_probability", 1.0))))
        rebuild_cost_tokens = max(0, int(meta.get("rebuild_cost_tokens", max(1, int(row.get("cacheable_tokens", 0))))))
        remaining_ttl = max(0.0, float(row.get("expires_at", 0.0)) - now)
        return reuse_probability * rebuild_cost_tokens * remaining_ttl

    def plan(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        provider: str,
        model: str,
        ttl_seconds: int | None = None,
        reorder: bool = True,
        now: float | None = None,
        cache_profile: str = "default",
        reuse_probability: float = 1.0,
        rebuild_cost_tokens: int | None = None,
    ) -> CachePlan:
        now = time.time() if now is None else float(now)
        provider_name = provider.strip().casefold() or "unknown"
        profile = str(cache_profile).strip() or "default"
        ttl = int(ttl_seconds or _PROVIDER_TTLS.get(provider_name, 600))
        stable_rows = [dict(row) for row in messages if self._stable_message(row)]
        volatile_rows = [dict(row) for row in messages if not self._stable_message(row)]
        ordered = [*stable_rows, *volatile_rows] if reorder else [dict(row) for row in messages]
        stable_prefix = [self._clean(row) for row in ordered[:len(stable_rows)]]
        stable_material: Any = stable_prefix
        if profile != "default":
            stable_material = {"cache_profile": profile, "stable_prefix": stable_prefix}
        stable_hash = sha256_bytes(canonical_json(stable_material))
        segments: list[CacheSegment] = []
        for row in ordered:
            clean = self._clean(row)
            raw = canonical_json(clean)
            stable = self._stable_message(row)
            segments.append(CacheSegment(str(row.get("role") or "unknown"), stable, len(raw), max(1, len(raw) // 4), sha256_bytes(raw), "stable-prefix" if stable else "volatile-tail"))
        plan = CachePlan(
            provider=provider_name,
            model=model,
            stable_prefix_hash=stable_hash,
            stable_messages=len(stable_rows),
            volatile_messages=len(volatile_rows),
            cacheable_tokens=sum(item.tokens_estimate for item in segments if item.stable),
            volatile_tokens=sum(item.tokens_estimate for item in segments if not item.stable),
            ttl_seconds=ttl,
            expires_at=now + ttl,
            refresh_after=now + ttl * 0.75,
            reordered=reorder and ordered != list(messages),
            segments=tuple(segments),
            cache_profile=profile,
        )
        probability = self._validate_reuse_probability(reuse_probability)
        rebuild = max(0, int(plan.cacheable_tokens if rebuild_cost_tokens is None else rebuild_cost_tokens))
        self._save(plan, now=now, reuse_probability=probability, rebuild_cost_tokens=rebuild)
        return plan

    def _save(self, plan: CachePlan, *, now: float, reuse_probability: float, rebuild_cost_tokens: int) -> None:
        current = read_json(self.path, {}) or {}
        plans = dict(current.get("plans") or {})
        governance = read_json(self.governance_path, {}) or {}
        entries = dict(governance.get("entries") or {})
        receipts = list(governance.get("receipts") or [])

        expired_keys = sorted(
            key for key, row in plans.items()
            if float((row or {}).get("expires_at", 0.0)) <= now
        )
        for key in expired_keys:
            plans.pop(key, None)
            entries.pop(key, None)

        key = self._cache_key(plan)
        plans[key] = asdict(plan)
        entries[key] = {
            "reuse_probability": reuse_probability,
            "rebuild_cost_tokens": rebuild_cost_tokens,
            "admitted_at": now,
        }

        evicted_keys: list[str] = []
        while len(plans) > self.max_entries:
            victim = min(
                plans,
                key=lambda candidate: (
                    self._entry_value(plans[candidate], entries.get(candidate, {}), now),
                    candidate,
                ),
            )
            plans.pop(victim, None)
            entries.pop(victim, None)
            evicted_keys.append(victim)

        admitted = key in plans
        candidate_value = reuse_probability * rebuild_cost_tokens * max(0.0, float(plan.expires_at) - now)
        if not admitted:
            reason = "capacity-lowest-value"
        elif evicted_keys:
            reason = "admitted-evicted-lower-value"
        elif expired_keys:
            reason = "admitted-after-expiry-reclamation"
        else:
            reason = "admitted-within-capacity"

        receipt = {
            "schema_version": self.GOVERNANCE_SCHEMA_VERSION,
            "decision": "admit" if admitted else "reject",
            "reason": reason,
            "cache_key": key,
            "reuse_probability": reuse_probability,
            "ttl_seconds": int(plan.ttl_seconds),
            "rebuild_cost_tokens": rebuild_cost_tokens,
            "value_score": candidate_value,
            "max_entries": self.max_entries,
            "expired_keys": expired_keys,
            "evicted_keys": evicted_keys,
            "retained_entries": len(plans),
        }
        receipts.append(receipt)
        receipts = receipts[-self.GOVERNANCE_RECEIPT_LIMIT:]

        atomic_write_json(self.path, {"plans": plans, "updated_at": time.time()})
        atomic_write_json(
            self.governance_path,
            {
                "schema_version": self.GOVERNANCE_SCHEMA_VERSION,
                "entries": entries,
                "receipts": receipts,
                "updated_at": time.time(),
            },
        )

    def maintain(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        current = read_json(self.path, {}) or {}
        plans = dict(current.get("plans") or {})
        governance = read_json(self.governance_path, {}) or {}
        entries = dict(governance.get("entries") or {})
        receipts = list(governance.get("receipts") or [])

        expired_keys = sorted(
            key for key, row in plans.items()
            if float((row or {}).get("expires_at", 0.0)) <= now
        )
        for key in expired_keys:
            plans.pop(key, None)
            entries.pop(key, None)

        evicted_keys: list[str] = []
        while len(plans) > self.max_entries:
            victim = min(
                plans,
                key=lambda candidate: (
                    self._entry_value(plans[candidate], entries.get(candidate, {}), now),
                    candidate,
                ),
            )
            plans.pop(victim, None)
            entries.pop(victim, None)
            evicted_keys.append(victim)

        changed = bool(expired_keys or evicted_keys)
        if changed:
            receipt = {
                "schema_version": self.GOVERNANCE_SCHEMA_VERSION,
                "decision": "maintain",
                "reason": "expired-or-capacity-reclamation",
                "cache_key": None,
                "reuse_probability": None,
                "ttl_seconds": None,
                "rebuild_cost_tokens": None,
                "value_score": None,
                "max_entries": self.max_entries,
                "expired_keys": expired_keys,
                "evicted_keys": evicted_keys,
                "retained_entries": len(plans),
            }
            receipts.append(receipt)
            receipts = receipts[-self.GOVERNANCE_RECEIPT_LIMIT:]
            atomic_write_json(self.path, {"plans": plans, "updated_at": time.time()})
            atomic_write_json(
                self.governance_path,
                {
                    "schema_version": self.GOVERNANCE_SCHEMA_VERSION,
                    "entries": entries,
                    "receipts": receipts,
                    "updated_at": time.time(),
                },
            )
        return {
            "changed": changed,
            "expired_keys": expired_keys,
            "evicted_keys": evicted_keys,
            "retained_entries": len(plans),
        }

    def governance_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        current = read_json(self.governance_path, {}) or {}
        rows = list(current.get("receipts") or [])
        count = max(0, int(limit))
        if count == 0:
            return []
        return [dict(row) for row in rows[-count:]]

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
