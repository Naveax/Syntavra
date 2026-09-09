# TE-P0-07 Cache Break-Even and Eviction Governor

Updated: **2026-09-09**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Purpose

Prevent stale or low-value prompt-cache plans from remaining pinned without a bounded, explainable economic decision.

## Reconciliation classification

`HARDEN + CERTIFY`

`PromptCacheOptimizer` remains the canonical plan store. This pass adds governance to that owner instead of creating a second cache planner.

## Deterministic governor

Each persisted candidate receives explicit inputs:

- reuse probability;
- TTL / remaining TTL;
- rebuild cost in token-equivalent work;
- bounded cache capacity.

The keep-value ordering is:

`reuse_probability * rebuild_cost_tokens * remaining_ttl_seconds`

This is an ordering primitive, not a provider-price or billed-token claim.

When capacity is exceeded, the lowest-value plan is evicted first. Equal values use the full cache key as the deterministic lexicographic tie-break. Expired plans are reclaimed before capacity selection and can also be reclaimed by explicit maintenance.

## Receipt isolation

Machine-readable governance state is stored at:

- `cache/governance.json`

The public/native parity surface remains:

- `cache/plans.json`

Governance metadata is intentionally kept out of `plans.json`, because the Remaining-71 Python/Rust parity validators compare that object structurally.

Receipts are bounded to the latest 256 decisions and include decision/reason, cache key, reuse probability, TTL, rebuild cost, value score, capacity, expired keys, evicted keys and retained entry count.

## Regression coverage

`tests/runtime/test_cache_eviction_governor.py` proves reuse probability, rebuild cost and TTL affect eviction; expiry reclamation and explicit maintenance work; equal-value ties are deterministic; receipts stay bounded and machine-readable; invalid reuse probability fails closed.

## TE-P0-06 prerequisite

The dedicated exact-head workflow first re-runs the existing `CacheProviderBudgetEngine` tests and certifier. TE-P0-07 therefore cannot become green while its cache-break attribution prerequisite is red.

## Exact-head gate

Dedicated workflow: `.github/workflows/token-economy-p0-06-07-cache-governance.yml`

Candidate contract: `contracts/python/token-economy-p0-07-cache-eviction-governor-v1.json`

Certifier: `tools/certify_token_economy_p0_07_cache_eviction_governor.py`

## Claim boundary

This certifies deterministic local admission/eviction behavior and bounded receipts only. It does not prove hosted-provider retention behavior, provider-billed token savings, or external superiority.
