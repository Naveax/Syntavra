# TE-P0-03 No-Change Result Elision

Updated: **2026-09-08**  
Status: STRUCTURAL IMPLEMENTATION ADMITTED / PROVIDER-SAVINGS CLAIM CLOSED

## Purpose

Avoid paying provider context repeatedly for a tool result that is provably the same logical view and the same semantic state.

This hardening is conservative. Identical bytes alone are not enough to elide a result when semantic metadata changed or the prior body cannot still be recovered/observed safely.

## Canonical owner

- `syntavra_runtime/agent_context_runtime.py`
- `ConstantContextState.ingest`

Regression coverage is currently carried by:

- `tests/runtime/test_pre_model_ingestion_fold_gate.py`
- `tests/runtime/test_active_context_supersession_graph.py`
- `tests/runtime/test_token_economy_rc1_runtime.py`

Exact-head deterministic-foundation CI has admitted the implementation on the active branch.

## Implemented contract

A repeated logical view becomes `UNCHANGED` with zero preview only when all of the following are true:

1. the canonical logical stream identity matches;
2. the content SHA-256 matches;
3. semantic metadata fingerprints match after excluding provider-visibility bookkeeping;
4. the canonical body is still active **or** exact recovery remains available.

The receipt preserves tool/view identity, content hash, round/provenance and exact artifact linkage while setting provider-visible evidence bytes to zero.

Caller key drift is not treated as a semantic change when the actual logical view identity is stable.

## Fail-closed rules

No-change elision is refused when:

- semantic metadata or invalidation state changes;
- search/query shape is incomplete and cross-generation identity cannot be proven;
- the prior body is neither active nor exactly recoverable;
- mandatory failure/security/verifier evidence requires retention.

In those cases normal supersession/retention rules apply. The system must not manufacture equivalence from matching bytes alone.

## Claim boundary

This is structural runtime evidence only.

`visible_bytes == 0` for an eligible repeated receipt is not, by itself, a provider-billed token-savings claim. Provider savings remain closed until paired provider-observed receipts under equivalent frozen tasks and verifiers satisfy the existing proof gates.

## Next dependency

TE-P0-04 Causal History Skeleton / Constant-Context Tool Loop must prove that these compact receipts stay bounded over long sessions rather than merely shrinking one repeated result.
