# TE-P0-08 Query Pushdown Reduction

Updated: **2026-09-08**  
Status: ENGINE-LAYER IMPLEMENTATION CANDIDATE / GATEWAY INTEGRATION STILL OPEN

## Reconciliation classification

`EXISTS + EXTEND + CERTIFY`

Canonical owner:

- `syntavra_runtime/agent_retrieval.py`
- `QueryPushdownEngine`

The existing owner already supplied:

- field projection;
- row limiting;
- allow-listed predicate filtering;
- fused `search_inspect` selection;
- fail-closed rejection of unknown provider-controlled fields/filters.

This pass extends that owner rather than creating a parallel query engine.

## Added reduction operators

The local reduction API now supports:

- `count`
- `sum`
- `min`
- `max`
- `group`
- `sort`
- `top_k`
- deterministic `sample`

Numeric aggregation is restricted to allow-listed numeric graph fields. Grouping is restricted to allow-listed public dimensions. Sort/top-k requires the ordering field to be part of the provider-visible projection so hidden fields cannot become an ordering side channel.

## Exact-before-lossy invariant

Every `reduce(...)` call requires an exact capture callback **before** projection or aggregation is returned.

The exact capture receives the filtered raw candidate window locally and must return both:

- an artifact identity;
- an exact recovery handle.

If exact capture is absent or incomplete, the reduction fails closed.

Forbidden/internal raw metadata may therefore remain recoverable locally without becoming provider-visible merely because aggregation is available.

## Candidate-window semantics

The current graph abstraction exposes bounded `query(..., limit=N)` retrieval, not a global aggregate API. Reduction therefore operates over the bounded candidate window and explicitly reports:

- `source_window_limit`
- `source_window_complete`

If the graph returns exactly the cap, completeness is **not** assumed. Consumers must not reinterpret a bounded-window count/group/min/max as a global repository fact.

A later server-native graph aggregate may upgrade this when the backend can prove global completeness.

## Privacy / capability boundary

- unknown fields fail before graph access;
- unknown filters fail before graph access;
- grouping on internal fields is rejected;
- numeric aggregation on internal/non-numeric fields is rejected;
- sort/top-k by hidden fields is rejected;
- exact local capture is not automatically provider-visible.

## Regression and benchmark

Regression:

- `tests/runtime/test_query_pushdown_reduction.py`

Local structural benchmark:

- `benchmarks/query_pushdown_reduction_benchmark.py`

The benchmark stores a large internal raw candidate set exactly, then exposes only a grouped compact result. The >90% byte-reduction gate is local structural evidence, not provider-billed proof.

## Exact-head workflow

- `.github/workflows/query-pushdown-reduction.yml`

## Still open before TE-P0-08 global completion

1. integrate `search_reduce` into `GatewayPatchProvider` with exact raw capture through the existing EvidenceStore/externalizer owner;
2. expose the exact source artifact/handle in the compact agent receipt without replaying raw rows;
3. add backend-native aggregation capability detection when a graph implementation can prove global completeness;
4. add provider-observed paired evidence before any provider savings claim.

## Next

After engine-layer admission, integrate the gateway action, then reconcile TE-P0-10 Verifier-Gated Inference Skip against its existing cache/identity/verifier owner.
