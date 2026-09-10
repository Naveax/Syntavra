# TE-P0-02 Active Context Supersession Graph

Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD CI REQUIRED  
Date: 2026-09-08  
Canonical runtime owner: `syntavra_runtime/agent_context_runtime.py`

## Purpose

Stop stale provider-visible evidence bodies from accumulating while preserving exact lineage and refusing unsafe equivalence assumptions.

The governing rule is conservative:

`prove same logical view -> supersede body -> retain exact handle`

If logical-view equivalence or exact recovery is not proven, Syntavra keeps the evidence active and accepts a budget failure instead of silently deleting it.

## Implemented contract

- caller keys are not treated as logical evidence identities;
- file-read streams bind path plus exact line range;
- repository-search streams bind query, projected fields and filters only when the complete query shape is supplied;
- incomplete search/query-shape metadata disables cross-generation supersession rather than guessing equivalence;
- diff, impact and verifier runs have deterministic stream identities;
- explicit `supersession_identity` can bind a caller-proven logical stream;
- `invalidation_fingerprint` is recorded between versions but is not used to disguise a changed state as a different logical stream;
- recoverable stale bodies become `SUPERSEDED_TO_HANDLE` causal receipts;
- causal receipts record old/new content identities, exact handles and invalidation changes;
- mandatory failure/security/verifier evidence remains pinned;
- evidence without an exact recovery handle remains pinned;
- max-active pressure cannot evict the newly admitted canonical state merely because older pinned evidence exists;
- stale bounded-recall bodies are removed when a newer canonical state supersedes their source stream;
- superseded artifacts remain exactly recoverable from the EvidenceStore.

## Security and correctness boundary

Supersession is not summarization. It is allowed only when Syntavra can identify two observations as versions of the same logical view. Ambiguous retrieval shapes are treated as distinct generations. Mandatory evidence and unrecoverable bodies are never dropped to make a token budget look better.

## Evidence

Dedicated regression coverage:

- `tests/runtime/test_active_context_supersession_graph.py`
- `tests/runtime/test_pre_model_ingestion_fold_gate.py`

Dedicated CI:

- `.github/workflows/token-economy-deterministic-foundations.yml`

Provider-savings claims remain closed until paired provider-observed receipts exist. This implementation is structural/runtime evidence, not a billed-token superiority claim.
