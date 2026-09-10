# TE-P0-14 OpenAI Prompt Cache Modernization

Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED  
Date: 2026-09-09  
Backlog owner: `TE-P0-14 OpenAI Prompt Cache Modernization`

## Purpose

Modernize the existing `ProviderGateway` and `PromptCacheOptimizer` OpenAI cache path without creating a second gateway or weakening legacy compatibility.

## Current OpenAI contract used by this implementation

The implementation follows the current OpenAI prompt-caching documentation checked on 2026-09-09:

- GPT-5.6 and later use `prompt_cache_options.ttl` for cache lifetime control;
- the currently supported `ttl` value is `30m`, which is also the default;
- earlier models continue to use `prompt_cache_retention` where supported;
- provider usage may report `input_tokens_details.cache_write_tokens`;
- `cache_write_tokens` are a subset of input tokens used for cache-write pricing/diagnostics and therefore must not be added to total provider tokens a second time.

Reference: `https://developers.openai.com/api/docs/guides/prompt-caching`

## Implementation

### Capability-gated request profiles

`syntavra_runtime/openai_prompt_cache.py` resolves one mutually exclusive profile:

- `openai-responses-gpt56-v1` for direct OpenAI Responses requests using GPT-5.6 or later;
- `openai-responses-legacy-v1` for earlier direct Responses models;
- `openai-chat-legacy-v1` for the older message-shaped OpenAI lane;
- `openai-azure-legacy-v1` for the explicit Azure compatibility alias.

The modern lane emits `prompt_cache_key` plus `prompt_cache_options={mode, ttl}` and does not automatically emit `prompt_cache_retention`. The legacy lane preserves the existing retention behavior. Supplying controls from both families fails closed instead of silently creating a mixed request.

### Stable-prefix isolation

`PromptCacheOptimizer` now accepts an internal `cache_profile` identity. Non-default profile identity is included in its stable-prefix fingerprint and plan-store key. `ProviderGateway` also includes the OpenAI cache profile in its cache identity.

Therefore two adapters that canonicalize to the same provider/model/request body cannot accidentally share Syntavra cache identity when their provider cache semantics differ.

Non-OpenAI providers retain the default profile and do not receive an unnecessary cache-key rewrite.

### Provider diagnostics

`ProviderCallObservationLedger` persists `cache_write_tokens` as an additive diagnostic column with migration from existing databases. The public observation schema version remains `2` because the field is backward-compatible and existing fields retain their semantics.

`provider_total_tokens` deliberately remains:

`fresh_input_tokens + cached_input_tokens + output_tokens + reasoning_tokens`

`cache_write_tokens` are not added again because they are already included in provider-reported input tokens.

`ProviderGateway.capture()` also exposes the cache-write diagnostic beside normalized usage when present.

## Regression gates

`tests/runtime/test_openai_prompt_cache_modernization.py` verifies:

1. GPT-5.6 Responses uses the modern cache-control family and never auto-adds legacy retention;
2. earlier OpenAI models retain the legacy path;
3. Azure compatibility does not silently inherit direct-OpenAI modern semantics;
4. modern and legacy profiles cannot share cache key or stable-prefix fingerprint;
5. mixed control families fail closed;
6. gateway usage capture exposes cache-write diagnostics without token double counting;
7. provider observation persists the same diagnostic while preserving provider-total semantics.

The dedicated CI additionally reruns the existing provider-gateway and provider-native-context-editing regressions.

## Claim boundary

This milestone proves deterministic request-building, compatibility separation and accounting semantics only. It does **not** prove provider-billed token savings or cache-hit-rate improvement. Those claims remain closed until paired provider-observed receipts meet the repository's existing evidence requirements.
