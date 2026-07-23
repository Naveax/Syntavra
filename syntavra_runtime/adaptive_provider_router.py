from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ProviderCandidate:
    provider: str
    model: str
    available: bool = True
    quota_remaining: float = 1.0
    rate_limited_until: float = 0.0
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    latency_ms: float = 0.0
    quality: float = 0.5
    max_complexity: str = "reasoning"
    context_window: int = 0
    account: str = "default"


@dataclass(frozen=True)
class ProviderRoute:
    provider: str
    model: str
    account: str
    complexity: str
    score: float
    reasons: tuple[str, ...]
    fallbacks: tuple[tuple[str, str, str], ...]
    receipt_hash: str


_COMPLEXITY = {"simple": 0, "medium": 1, "complex": 2, "reasoning": 3}


class ModelComplexityClassifier:
    def classify(self, task: str, *, changed_files: int = 0, token_estimate: int = 0) -> str:
        corpus = task.casefold()
        score = 0
        score += 2 if any(term in corpus for term in ("architecture", "security", "migration", "race condition", "formal", "proof", "root cause")) else 0
        score += 1 if any(term in corpus for term in ("refactor", "debug", "benchmark", "cross-repo", "dependency")) else 0
        score += 1 if changed_files >= 8 else 0
        score += 1 if token_estimate >= 16_000 else 0
        if score >= 4:
            return "reasoning"
        if score >= 2:
            return "complex"
        if score == 1:
            return "medium"
        return "simple"


class AdaptiveProviderRouter:
    def __init__(self, candidates: Iterable[ProviderCandidate]):
        self.candidates = tuple(candidates)

    def route(self, task: str, *, changed_files: int = 0, token_estimate: int = 0, now: float | None = None, prefer_subscription: bool = True) -> ProviderRoute:
        now = time.time() if now is None else float(now)
        complexity = ModelComplexityClassifier().classify(task, changed_files=changed_files, token_estimate=token_estimate)
        needed = _COMPLEXITY[complexity]
        ranked: list[tuple[float, ProviderCandidate, tuple[str, ...]]] = []
        for row in self.candidates:
            reasons: list[str] = []
            if not row.available:
                continue
            if row.rate_limited_until > now:
                continue
            if row.quota_remaining <= 0:
                continue
            if _COMPLEXITY.get(row.max_complexity, 0) < needed:
                continue
            if row.context_window and token_estimate > row.context_window:
                continue
            blended_cost = row.input_cost_per_million + row.output_cost_per_million * 2
            score = row.quality * 60 - blended_cost * 2 - row.latency_ms / 1000
            score += min(20.0, row.quota_remaining * 20)
            if prefer_subscription and blended_cost == 0:
                score += 15
                reasons.append("subscription-or-free-quota")
            reasons.extend((f"quality={row.quality:.3f}", f"quota={row.quota_remaining:.3f}", f"latency_ms={row.latency_ms:.1f}", f"cost_index={blended_cost:.4f}"))
            ranked.append((score, row, tuple(reasons)))
        if not ranked:
            raise RuntimeError("no provider satisfies availability, quota, rate-limit, context and complexity constraints")
        ranked.sort(key=lambda item: (-item[0], item[1].provider, item[1].model, item[1].account))
        score, selected, reasons = ranked[0]
        fallback = tuple((row.provider, row.model, row.account) for _, row, _ in ranked[1:6])
        body = {"provider": selected.provider, "model": selected.model, "account": selected.account, "complexity": complexity, "score": score, "reasons": reasons, "fallbacks": fallback}
        return ProviderRoute(**body, receipt_hash=sha256_bytes(canonical_json(body)))

    @staticmethod
    def from_mappings(rows: Iterable[Mapping[str, Any]]) -> "AdaptiveProviderRouter":
        return AdaptiveProviderRouter(ProviderCandidate(**dict(row)) for row in rows)
