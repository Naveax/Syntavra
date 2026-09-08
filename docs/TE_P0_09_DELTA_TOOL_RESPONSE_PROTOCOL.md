# TE-P0-09 Delta Tool Response Protocol

Updated: **2026-09-08**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Purpose

Avoid resending near-identical repeated tool bodies while preserving exact current evidence and refusing delta reuse when baseline equivalence cannot be proven.

## Reconciliation classification

`EXISTS + HARDEN + CERTIFY`

The canonical owner already existed before this pass:

- `syntavra_runtime/tool_externalization.py`
- `ToolOutputExternalizer`

Existing capabilities retained rather than duplicated:

- stable tool/command/path stream keys;
- exact raw artifact persistence;
- content-addressed segment storage;
- SHA-256 segment comparison;
- Merkle verification;
- changed-segment projection;
- `baseline_artifact_id` lineage;
- `delta-externalized` provider preview;
- exact reveal/search/restore paths.

## Hardening added in this pass

Delta reuse is now admitted only when the candidate baseline is all of the following:

1. in the same scope + stable tool/command/path stream;
2. from the same externalization policy digest;
3. from the same classified output family;
4. quality-gate admitted;
5. tagged with the same normalized semantic invalidation fingerprint;
6. backed by an exact evidence handle that still verifies.

If any condition fails, the current result remains independently exact and the delta path fails closed to the normal full-current externalization protocol.

The exact baseline status is attributed in `metadata.delta_protocol.baseline_status`; examples include:

- `VALID`
- `NO_BASELINE`
- `CURRENT_INVALIDATION_MISSING`
- `BASELINE_INVALIDATION_MISSING`
- `INVALIDATION_MISMATCH`
- `POLICY_MISMATCH`
- `FAMILY_MISMATCH`
- `BASELINE_QUALITY_UNPROVED`
- `BASELINE_EXACT_UNAVAILABLE`

## Dedupe invalidation

The normalized invalidation fingerprint is part of the dedup identity. Identical bytes from a different semantic generation are therefore not silently returned as an old dedup artifact.

Arbitrary caller labels are normalized to SHA-256. A caller-supplied 64-hex fingerprint remains byte-stable after lowercase normalization.

## Delta receipt

An emitted delta preview carries:

- exact current artifact identity;
- exact current evidence handle;
- baseline artifact identity;
- normalized invalidation fingerprint;
- unchanged-segment ratio;
- changed-segment count;
- bounded changed evidence;
- current summary/facets according to the externalization family.

The full current raw object is stored independently before lossy provider projection. Delta reconstruction is never the sole source of truth.

## Regression coverage

- `tests/runtime/test_tool_externalization_v2.py`
- `tests/runtime/test_delta_tool_response_protocol.py`

Coverage includes:

- stable fingerprint delta admission;
- missing fingerprint fallback;
- changed fingerprint fallback;
- same-bytes/different-generation dedupe refusal;
- missing exact baseline fallback;
- exact current round-trip after every path.

## Polling-loop benchmark

`benchmarks/delta_tool_response_benchmark.py` compares cumulative provider-visible candidate bytes against replaying each full current polling body across 24 rounds.

The local structural gate requires:

- every current artifact to round-trip exactly;
- every post-warmup round to use a validated delta baseline;
- candidate cumulative bytes below full-current replay;
- greater than 90% local structural byte reduction on the deterministic fixture.

This is **not** a provider-billed token claim.

## Exact-head gate

Dedicated workflow:

- `.github/workflows/delta-tool-response-protocol.yml`

Promotion requires the exact PR head to pass runtime regression, legacy externalization regression, polling benchmark and clean-head enforcement.

## Claim boundary

`delta visible bytes`, local structural bytes and internal externalization ratios do not prove hosted provider savings.

Provider savings remain closed until paired provider-observed receipts run equivalent frozen tasks/verifiers and include all recall/retry/fallback burden.

## Next reconciliation

After exact-head admission, continue with TE-P0-08 Query Pushdown aggregate/sort/top-k/sample and exact-artifact-before-lossy-aggregation gaps, then reconcile TE-P0-10 Verifier-Gated Inference Skip against its existing owner.
