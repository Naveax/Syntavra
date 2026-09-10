# TE-P0-04 Causal History Skeleton / Constant-Context Tool Loop

Updated: **2026-09-08**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Purpose

Keep provider-visible tool history approximately proportional to **current active evidence**, not to the number of tool rounds, without deleting evidence that cannot be recovered exactly.

The canonical runtime already separates current evidence from bounded causal receipts. This pass reconciles that existing behavior against TE-P0-04 and adds a dedicated long-session proof instead of creating a second context-history owner.

## Reconciliation classification

`EXISTS + HARDEN + CERTIFY`

Canonical owner:

- `syntavra_runtime/agent_context_runtime.py`
- `ConstantContextState`

Dedicated regression:

- `tests/runtime/test_causal_history_skeleton.py`

Exact-head workflow:

- `.github/workflows/token-economy-deterministic-foundations.yml`

## Implemented boundary

### Active evidence

Current recoverable evidence lives in the active set. Logical stream supersession removes stale recoverable bodies from provider-visible active context and leaves an exact handle-backed causal receipt.

Capacity pressure may evict only evidence with exact recovery. Mandatory or unrecoverable evidence remains pinned and provider-budget compilation fails closed if safety evidence cannot fit.

### Causal skeleton

Historical causal rows are bounded by `max_causal` and carry compact lineage such as:

- key/tool;
- status;
- stream identity;
- SHA-256;
- exact artifact handle;
- round/provenance;
- supersession target/invalidation attribution.

They do **not** replay raw tool bodies.

### Long-session invariant

The dedicated regression runs equivalent 32-round and 160-round sessions with four changing logical streams and exact externalization. It requires:

- active evidence count to remain four;
- causal receipt count to remain eight;
- active and superseded evidence to retain exact handles;
- causal rows to be body-free;
- compiled provider bytes/tokens to remain within a small bounded delta despite a 5x increase in rounds;
- raw history replay to remain disabled.

This is a structural O(active-evidence + bounded-causal-receipts) regression, not a provider billing claim.

## Still open inside the wider TE-P0-04 family

The following must be reconciled with their existing owners before TE-P0-04 is declared globally complete:

1. current-diff representation as changed hunks/symbols plus exact artifact handle where whole repeated diff bodies are still exposed;
2. successful verifier-log collapse to status/count/receipt with minimal failure evidence pinned;
3. any host/provider adapter that bypasses `ConstantContextState`;
4. paired provider-observed evidence for savings claims.

No new implementation should duplicate an existing diff/verifier/externalization owner merely to satisfy a checklist label.

## Safety invariants

- exact raw evidence remains authoritative;
- unrecoverable evidence is never silently evicted;
- mandatory user/security/failure/verifier evidence is never dropped to hit a token target;
- recalls are bounded and scope-bound;
- recalled tool evidence remains untrusted data;
- compilation fails closed when irreducible pinned evidence exceeds the provider budget.

## Promotion rule

The candidate is admitted only after the exact PR-head `Token Economy Deterministic Foundations` workflow succeeds with the new causal-history regression. Until then the implementation remains a candidate.

After admission, reconcile TE-P0-09 Delta Tool Response Protocol next within the TE-1 wave, while treating existing Query Pushdown and Inference Skip owners as reconciliation targets rather than blindly reimplementing them.
