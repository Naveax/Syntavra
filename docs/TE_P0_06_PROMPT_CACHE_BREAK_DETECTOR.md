# TE-P0-06 Prompt Cache Break Detector

Updated: **2026-09-09**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Purpose

Make prompt-cache misses attributable instead of treating every miss as an opaque provider event.

## Reconciliation classification

`EXISTS + CERTIFY`

The canonical behavior already exists in:

- `syntavra_runtime/cache_provider_budget.py`
- `CacheProviderBudgetEngine`
- `tests/runtime/test_cache_provider_budget_v1.py`
- `tools/certify_cache_provider_budget_v1.py`
- `contracts/python/cache-provider-budget-v1.json`

No parallel cache-break detector is introduced.

## Attributable causes

The existing owner distinguishes deterministic causes including:

- initial plan;
- provider change;
- model change;
- stable-prefix change;
- previous cache expiry;
- no break.

The provider/model/cache-key inputs remain visible in the decision receipt and stable-prefix mutation is regression-tested.

## Exact-head gate

TE-P0-06 is revalidated as the prerequisite stage of:

- `.github/workflows/token-economy-p0-06-07-cache-governance.yml`

That workflow runs the existing cache/provider regression suite and certifier before TE-P0-07 is allowed to certify.

## Claim boundary

This is local structural/runtime cache-break attribution. It is not provider-billed cache-hit proof and does not upgrade any external savings claim.
