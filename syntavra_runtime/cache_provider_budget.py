from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .adaptive_provider_router import AdaptiveProviderRouter, ModelComplexityClassifier, ProviderCandidate
from .prompt_cache_optimizer import CachePlan, PromptCacheOptimizer
from .provider_account_pool import ProviderAccountPool
from .provider_gateway import ProviderGateway
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ProviderBudgetPolicy:
    max_expected_cost_usd: float | None = None
    min_quality: float = 0.0
    min_quota_remaining: float = 0.0
    require_prompt_cache: bool = False
    require_explicit_prompt_cache: bool = False
    prefer_subscription: bool = True
    expected_requests: int = 1
    cache_write_multiplier: float = 1.0
    cache_read_multiplier: float = 0.2

    def __post_init__(self) -> None:
        if self.max_expected_cost_usd is not None:
            value = float(self.max_expected_cost_usd)
            if not math.isfinite(value) or value < 0:
                raise ValueError("max_expected_cost_usd must be finite and non-negative")
            object.__setattr__(self, "max_expected_cost_usd", value)
        if not 0.0 <= float(self.min_quality) <= 1.0:
            raise ValueError("min_quality must be between 0 and 1")
        if not 0.0 <= float(self.min_quota_remaining) <= 1.0:
            raise ValueError("min_quota_remaining must be between 0 and 1")
        if int(self.expected_requests) < 1:
            raise ValueError("expected_requests must be positive")
        if float(self.cache_write_multiplier) < 0 or float(self.cache_read_multiplier) < 0:
            raise ValueError("cache multipliers must be non-negative")
        object.__setattr__(self, "expected_requests", int(self.expected_requests))
        object.__setattr__(self, "min_quality", float(self.min_quality))
        object.__setattr__(self, "min_quota_remaining", float(self.min_quota_remaining))
        object.__setattr__(self, "cache_write_multiplier", float(self.cache_write_multiplier))
        object.__setattr__(self, "cache_read_multiplier", float(self.cache_read_multiplier))


@dataclass(frozen=True)
class CandidateBudgetEstimate:
    provider: str
    model: str
    account: str
    prompt_cache_mode: str
    input_tokens: int
    cacheable_tokens: int
    volatile_tokens: int
    output_tokens: int
    baseline_cost_usd: float
    optimized_cost_usd: float
    savings_usd: float
    savings_ratio: float
    capability_ok: bool
    budget_ok: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CacheProviderBudgetDecision:
    provider: str
    model: str
    account: str
    complexity: str
    prompt_cache_mode: str
    stable_prefix_hash: str
    cacheable_tokens: int
    volatile_tokens: int
    cache_expires_at: float
    cache_refresh_after: float
    baseline_cost_usd: float
    expected_cost_usd: float
    expected_savings_usd: float
    savings_ratio: float
    cache_bust_reasons: tuple[str, ...]
    fallbacks: tuple[tuple[str, str, str], ...]
    rejected: tuple[CandidateBudgetEstimate, ...]
    receipt_hash: str


class BudgetExhaustedError(RuntimeError):
    def __init__(self, message: str, *, receipt: Mapping[str, Any]):
        super().__init__(message)
        self.receipt = dict(receipt)


class CacheProviderBudgetEngine:
    """Provider/cache budget authority composed from existing Syntavra primitives.

    The engine owns no credential store and introduces no persistence layer. Provider
    account/quota/circuit state stays in ProviderAccountPool; cache plan persistence stays
    in PromptCacheOptimizer. This layer only filters, negotiates and emits deterministic
    decision receipts.
    """

    schema_version = 1

    def __init__(self, *, account_pool: ProviderAccountPool, cache_optimizer: PromptCacheOptimizer):
        self.account_pool = account_pool
        self.cache_optimizer = cache_optimizer

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "provider_aware_prompt_cache_compiler": True,
            "provider_budget_engine": True,
            "cache_roi": True,
            "cache_bust_attribution": True,
            "provider_capability_negotiation": True,
            "deterministic_fallback_policy": True,
            "provider_account_pool_reused": True,
            "prompt_cache_optimizer_reused": True,
            "adaptive_provider_router_reused": True,
            "provider_gateway_capabilities_reused": True,
            "parallel_persistent_store": False,
            "credential_ownership": False,
            "public_cli_route": False,
        }

    @staticmethod
    def _message_token_estimate(messages: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
        cacheable = 0
        volatile = 0
        for row in messages:
            cleaned = PromptCacheOptimizer._clean(row)
            raw = canonical_json(cleaned)
            estimate = max(1, len(raw) // 4)
            if PromptCacheOptimizer._stable_message(row):
                cacheable += estimate
            else:
                volatile += estimate
        return cacheable + volatile, cacheable, volatile

    @staticmethod
    def _capability_mode(provider: str, *, cacheable_tokens: int) -> tuple[str, dict[str, Any] | None]:
        try:
            capabilities = ProviderGateway.capabilities(provider)
        except ValueError:
            return "unsupported", None
        if cacheable_tokens <= 0:
            return "no-stable-prefix", capabilities
        if capabilities["explicit_prompt_cache"]:
            return "explicit", capabilities
        if capabilities["implicit_prompt_cache"]:
            return "implicit", capabilities
        return "stable-prefix-only", capabilities

    @staticmethod
    def _model_row_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            provider = str(row.get("provider") or "").strip().casefold()
            model = str(row.get("model") or "").strip()
            if not provider or not model:
                continue
            key = (provider, model)
            if key in result:
                raise ValueError(f"duplicate provider/model pricing row: {provider}/{model}")
            result[key] = dict(row)
        return result

    @staticmethod
    def _cost_estimate(
        candidate: ProviderCandidate,
        model_row: Mapping[str, Any],
        *,
        input_tokens: int,
        cacheable_tokens: int,
        volatile_tokens: int,
        output_tokens: int,
        expected_requests: int,
        cache_enabled: bool,
        policy: ProviderBudgetPolicy,
    ) -> tuple[float, float, float, float]:
        input_rate = max(0.0, float(model_row.get("input_cost_per_million", candidate.input_cost_per_million)))
        output_rate = max(0.0, float(model_row.get("output_cost_per_million", candidate.output_cost_per_million)))
        requests = max(1, int(expected_requests))
        baseline_input = input_tokens * requests
        baseline_output = output_tokens * requests
        baseline = (baseline_input * input_rate + baseline_output * output_rate) / 1_000_000.0

        if cache_enabled and cacheable_tokens > 0 and requests > 1:
            write_multiplier = max(0.0, float(model_row.get("cache_write_multiplier", policy.cache_write_multiplier)))
            read_multiplier = max(0.0, float(model_row.get("cache_read_multiplier", policy.cache_read_multiplier)))
            optimized_input = (
                volatile_tokens * requests
                + cacheable_tokens * write_multiplier
                + cacheable_tokens * read_multiplier * (requests - 1)
            )
        else:
            optimized_input = baseline_input
        optimized = (optimized_input * input_rate + baseline_output * output_rate) / 1_000_000.0
        savings = max(0.0, baseline - optimized)
        ratio = savings / baseline if baseline > 0 else 0.0
        return tuple(round(value, 10) for value in (baseline, optimized, savings, ratio))

    @staticmethod
    def _cache_bust_reasons(
        previous: CacheProviderBudgetDecision | None,
        *,
        provider: str,
        model: str,
        stable_prefix_hash: str,
        now: float,
    ) -> tuple[str, ...]:
        if previous is None:
            return ("initial-plan",)
        reasons: list[str] = []
        if previous.provider != provider:
            reasons.append("provider-changed")
        if previous.model != model:
            reasons.append("model-changed")
        if previous.stable_prefix_hash != stable_prefix_hash:
            reasons.append("stable-prefix-changed")
        if previous.cache_expires_at and now >= previous.cache_expires_at:
            reasons.append("previous-cache-expired")
        return tuple(reasons or ("none",))

    def plan(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        task: str,
        model_rows: Iterable[Mapping[str, Any]],
        output_tokens_estimate: int = 0,
        policy: ProviderBudgetPolicy | None = None,
        changed_files: int = 0,
        previous: CacheProviderBudgetDecision | None = None,
        now: float | None = None,
    ) -> CacheProviderBudgetDecision:
        policy = policy or ProviderBudgetPolicy()
        timestamp = float(now) if now is not None else 0.0
        if now is None:
            import time
            timestamp = time.time()
        output_tokens = max(0, int(output_tokens_estimate))
        input_tokens, cacheable_tokens, volatile_tokens = self._message_token_estimate(messages)
        rows = tuple(dict(row) for row in model_rows)
        index = self._model_row_index(rows)
        candidates = self.account_pool.candidates(rows, now=timestamp)
        complexity = ModelComplexityClassifier().classify(
            task,
            changed_files=changed_files,
            token_estimate=input_tokens + output_tokens,
        )

        accepted: list[ProviderCandidate] = []
        estimates: dict[tuple[str, str, str], CandidateBudgetEstimate] = {}
        rejected: list[CandidateBudgetEstimate] = []
        for candidate in candidates:
            model_row = index[(candidate.provider, candidate.model)]
            mode, capabilities = self._capability_mode(candidate.provider, cacheable_tokens=cacheable_tokens)
            reasons: list[str] = []
            capability_ok = capabilities is not None
            if capabilities is None:
                reasons.append("provider-capabilities-unknown")
            if policy.require_prompt_cache and (
                capabilities is None
                or not (capabilities["implicit_prompt_cache"] or capabilities["explicit_prompt_cache"])
            ):
                capability_ok = False
                reasons.append("prompt-cache-required")
            if policy.require_explicit_prompt_cache and (
                capabilities is None or not capabilities["explicit_prompt_cache"]
            ):
                capability_ok = False
                reasons.append("explicit-prompt-cache-required")
            if candidate.quality < policy.min_quality:
                reasons.append("quality-below-minimum")
            if candidate.quota_remaining < policy.min_quota_remaining:
                reasons.append("quota-below-reserve")
            if candidate.context_window and input_tokens + output_tokens > candidate.context_window:
                reasons.append("context-window-exceeded")

            cache_enabled = bool(
                capabilities
                and cacheable_tokens > 0
                and (capabilities["implicit_prompt_cache"] or capabilities["explicit_prompt_cache"])
            )
            baseline, optimized, savings, savings_ratio = self._cost_estimate(
                candidate,
                model_row,
                input_tokens=input_tokens,
                cacheable_tokens=cacheable_tokens,
                volatile_tokens=volatile_tokens,
                output_tokens=output_tokens,
                expected_requests=policy.expected_requests,
                cache_enabled=cache_enabled,
                policy=policy,
            )
            budget_ok = policy.max_expected_cost_usd is None or optimized <= policy.max_expected_cost_usd
            if not budget_ok:
                reasons.append("expected-cost-exceeds-budget")

            estimate = CandidateBudgetEstimate(
                provider=candidate.provider,
                model=candidate.model,
                account=candidate.account,
                prompt_cache_mode=mode,
                input_tokens=input_tokens,
                cacheable_tokens=cacheable_tokens,
                volatile_tokens=volatile_tokens,
                output_tokens=output_tokens,
                baseline_cost_usd=baseline,
                optimized_cost_usd=optimized,
                savings_usd=savings,
                savings_ratio=savings_ratio,
                capability_ok=capability_ok,
                budget_ok=budget_ok,
                reasons=tuple(sorted(set(reasons))),
            )
            estimates[(candidate.provider, candidate.model, candidate.account)] = estimate
            hard_reject = (
                not capability_ok
                or candidate.quality < policy.min_quality
                or candidate.quota_remaining < policy.min_quota_remaining
                or (candidate.context_window and input_tokens + output_tokens > candidate.context_window)
                or not budget_ok
            )
            if hard_reject:
                rejected.append(estimate)
            else:
                accepted.append(candidate)

        if not accepted:
            body = {
                "schema_version": self.schema_version,
                "decision": "ABSTAIN",
                "complexity": complexity,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "policy": asdict(policy),
                "rejected": [asdict(row) for row in sorted(rejected, key=lambda row: (row.provider, row.model, row.account))],
            }
            body["receipt_hash"] = sha256_bytes(canonical_json(body))
            raise BudgetExhaustedError("no provider satisfies capability, quota, context and budget constraints", receipt=body)

        route = AdaptiveProviderRouter(accepted).route(
            task,
            changed_files=changed_files,
            token_estimate=input_tokens + output_tokens,
            now=timestamp,
            prefer_subscription=policy.prefer_subscription,
        )
        selected_key = (route.provider, route.model, route.account)
        selected_estimate = estimates[selected_key]
        cache_plan: CachePlan = self.cache_optimizer.plan(
            messages,
            provider=route.provider,
            model=route.model,
            now=timestamp,
        )
        bust = self._cache_bust_reasons(
            previous,
            provider=route.provider,
            model=route.model,
            stable_prefix_hash=cache_plan.stable_prefix_hash,
            now=timestamp,
        )
        body = {
            "provider": route.provider,
            "model": route.model,
            "account": route.account,
            "complexity": route.complexity,
            "prompt_cache_mode": selected_estimate.prompt_cache_mode,
            "stable_prefix_hash": cache_plan.stable_prefix_hash,
            "cacheable_tokens": cache_plan.cacheable_tokens,
            "volatile_tokens": cache_plan.volatile_tokens,
            "cache_expires_at": cache_plan.expires_at,
            "cache_refresh_after": cache_plan.refresh_after,
            "baseline_cost_usd": selected_estimate.baseline_cost_usd,
            "expected_cost_usd": selected_estimate.optimized_cost_usd,
            "expected_savings_usd": selected_estimate.savings_usd,
            "savings_ratio": selected_estimate.savings_ratio,
            "cache_bust_reasons": bust,
            "fallbacks": route.fallbacks,
            "rejected": tuple(sorted(rejected, key=lambda row: (row.provider, row.model, row.account))),
        }
        receipt_body = {
            key: ([asdict(item) for item in value] if key == "rejected" else value)
            for key, value in body.items()
        }
        body["receipt_hash"] = sha256_bytes(canonical_json(receipt_body))
        return CacheProviderBudgetDecision(**body)
