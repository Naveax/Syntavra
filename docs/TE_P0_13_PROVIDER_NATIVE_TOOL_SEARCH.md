# TE-P0-13 Provider-Native Tool Search Adapter

Status: DETACHED CANDIDATE / EXACT-HEAD CI REQUIRED  
Authority: `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` TE-P0-13

## Purpose

TE-P0-13 removes repeated full tool-schema exposure at the provider boundary when the selected provider/model has a native deferred tool-search facility and an end-to-end benchmark has admitted it.

This milestone does not replace Syntavra's portable deferred discovery engine. `syntavra_runtime/deferred_tool_discovery.py` remains the provider-independent fallback/oracle. Native adaptation lives in `ProviderProxyRuntime` immediately before `ProviderGateway.prepare()`, so the exact rewritten request is the input to request hashing, caching policy, replay safety and evidence capture.

## Promotion rule

Native tool search is applied only when all of the following hold:

1. `ProxyConfig.native_tool_search` is not `off`;
2. `ProxyConfig.native_tool_search_benchmark_admitted` is true;
3. the canonical provider supports the requested native search family;
4. the concrete model is in the admitted capability family;
5. the request shape is compatible with that provider's native API contract;
6. at least one eligible tool can actually be deferred.

If any condition fails, the request remains portable and unmodified. Provider feature availability alone is not evidence that native search wins on verified latency, tokens or correctness.

## Anthropic

Current Anthropic tool search provides two server-side search tools:

- `tool_search_tool_bm25_20251119` for natural-language retrieval;
- `tool_search_tool_regex_20251119` for model-generated regex retrieval.

`auto` selects BM25. The adapter may defer only plain client-defined tools with a complete `name` and `input_schema`. The server tool itself is never deferred. An explicit `defer_loading: false` is respected.

Anthropic documents that deferred tools cannot carry incompatible prompt-cache control. Therefore a client tool with `cache_control` is preserved upfront instead of being silently rewritten into an invalid deferred definition.

The adapter is conservative about model compatibility: Claude 4.5+ families documented for tool search and current Claude 5-series families are admitted. Older/unknown model IDs fall back to local discovery.

## OpenAI Responses

OpenAI tool search is a Responses API facility. It is model-specific rather than provider-wide.

The adapter admits the currently verified tool-search families, including GPT-5.4, GPT-5.4 Pro, GPT-5.5, GPT-5.6 Sol/alias and GPT-6 Astra. Known unsupported variants such as GPT-5.4 nano, GPT-5.5 Pro and `chat-latest` fail closed.

Only Responses-style requests are rewritten. A Chat Completions `messages` request is not upgraded merely because its model also supports Responses.

For the first milestone, only top-level Responses function tools are deferred. Built-in tools, MCP tools, legacy nested Chat-Completions function shapes and unknown tool kinds are preserved untouched. When at least one function is deferred, a single hosted `{ "type": "tool_search" }` entry is added. An existing tool-search entry is never duplicated.

## Evidence and replay boundary

The rewrite happens before `ProviderGateway.prepare()` rather than after it. Therefore:

- the rewritten payload participates in the provider request hash;
- the same rewritten payload is stored as exact provider-request evidence;
- provider cache/replay decisions see the actual tool-bearing request;
- existing fail-closed rule that disables deterministic response replay for tool-bearing requests remains unchanged unless the caller explicitly opts into tool replay.

This avoids the particularly charming failure mode where telemetry proves a request different from the one actually sent upstream.

## Claim boundary

This milestone proves capability-gated request adaptation and portable fallback. It does **not** prove provider-billed savings. Native search promotion still requires the existing paired provider-observed benchmark/evidence path under equivalent task, verifier and cache conditions.

## Acceptance

Dedicated exact-head CI must prove:

1. provider/model capability detection is deterministic and fail-closed;
2. benchmark admission is mandatory before request mutation;
3. Anthropic BM25 and regex request shapes are deterministic;
4. Anthropic cache-control conflicts stay upfront;
5. OpenAI tool search is Responses-only and model-gated;
6. OpenAI built-ins are not accidentally deferred;
7. unsupported models/providers preserve portable payloads;
8. exact request evidence contains the native rewrite;
9. existing provider-gateway replay/cache tests still pass;
10. the repository is clean at the exact candidate head.
