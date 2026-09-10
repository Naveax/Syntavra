# TE-P0-12 Provider-Native Context Editing Adapter

Status: IMPLEMENTED CANDIDATE / EXACT-HEAD CI REQUIRED  
Authority: `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` TE-P0-12

## Purpose

This milestone adds capability-gated provider-native context editing to the existing model gateway instead of creating a parallel provider stack.

The canonical owners remain:

- `syntavra_runtime/model_gateway.py` for provider request/response behavior;
- `syntavra_runtime/provider_call_observation.py` for per-call provider usage evidence.

The Provider Token Envelope remains the provider input/output budget authority.

## Capability boundary

Provider-native behavior is never inferred merely from an OpenAI-compatible HTTP shape.

Current admitted capabilities are:

- Anthropic/Claude: selective server-side tool-result and thinking clearing is directly supported by the existing stateless Messages transport.
- OpenAI Responses: native context management/compaction is detected, but automatic use is not admitted in this milestone because correct continuation requires explicit state/opaque compaction-item integration.
- Generic OpenAI-compatible/local/Gemini transports: portable Syntavra context handling remains the fallback unless a provider-specific contract is separately admitted.

This prevents provider-specific request fields from leaking into nominally compatible endpoints that do not implement their semantics.

## Benchmark gate

Native editing is applied only when both are true:

1. `ContextEditingConfig.enabled`;
2. `ContextEditingConfig.benchmark_admitted`.

Configuration without benchmark admission is shadow/fallback state and sends no native context-management request.

This implements the backlog rule that provider-native editing is preferred only when the end-to-end benchmark wins. A smaller immediate prompt is not enough if cache invalidation, later reacquisition, retries or verifier failures make total verified cost worse.

## Anthropic request contract

When admitted, the gateway sends the current Anthropic context-management beta header and a deterministic edit order:

1. `clear_thinking_20251015`, when enabled;
2. `clear_tool_uses_20250919`, when enabled.

Tool clearing supports input-token trigger, recent tool-use retention, minimum clear size, excluded tools and optional clearing of tool inputs. Thinking clearing optionally retains a bounded number of recent thinking turns.

The ordering is deliberate because Anthropic requires thinking clearing to precede tool clearing when both strategies are present.

## Cleared-token receipt reconciliation

Anthropic reports applied edits in `response.context_management.applied_edits` and may include `cleared_input_tokens` per edit.

Syntavra now:

- exposes the summed value in `ModelResult.diagnostics.context_editing`;
- persists the same sum in `ProviderCallObservationLedger`;
- binds it to the hash of the exact provider response;
- migrates existing observation databases additively with a default of zero.

`cleared_input_tokens` is a context-editing diagnostic. It is **not** added to `provider_total_tokens`, because it is not an additional billed-token category. This also means the diagnostic alone cannot prove provider-billed savings.

## OpenAI boundary

The current OpenAI Responses API exposes request-level context management and `/responses/compact`. Compaction returns opaque continuation items and has its own usage accounting.

Syntavra therefore detects the capability but leaves `directly_admissible=false` in the current stateless `complete(messages)` transport. A later stateful adapter must preserve conversation/previous-response identity, opaque compaction items, usage receipts and fallback before automatic compaction can be promoted.

## Claim boundary

This milestone can claim:

- deterministic capability detection;
- benchmark-gated Anthropic native request construction;
- provider-reported cleared-token diagnostic reconciliation;
- portable fallback preservation.

It does not claim provider-billed token savings. Such a claim still requires paired provider-observed runs under equivalent frozen tasks, verifier contracts and cache conditions.

## Acceptance

Dedicated exact-head CI must prove:

1. Anthropic is directly admissible and reports cleared-token diagnostics;
2. OpenAI Responses compaction is detected but not auto-admitted;
3. unsupported/generic transports fail closed to portable fallback;
4. benchmark admission is required before request mutation;
5. Anthropic edit order and request fields are deterministic;
6. response cleared-token diagnostics are exposed without contaminating billed usage;
7. observation receipts persist cleared tokens while provider total remains unchanged;
8. the repository remains clean at the exact candidate head.
