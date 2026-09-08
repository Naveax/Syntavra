# TE-P0-08 Query Pushdown Gateway Integration

Updated: **2026-09-08**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Reconciliation classification

`EXISTS + INTEGRATE + CERTIFY`

The reduction engine already owns strict projection/filter validation and the local operators `count`, `sum`, `min`, `max`, `group`, `sort`, `top_k` and deterministic `sample`. This pass connects that engine to the production `GatewayPatchProvider` instead of creating a competing retrieval owner.

## Provider action

The model-facing action is `search_reduce`.

It accepts the same allow-listed query projection/filter controls as repository search plus:

- `operator`;
- `field` for numeric/sort/top-k operations;
- `group_by` for grouping;
- JSON boolean `descending` for sort/top-k;
- bounded `sample_seed` for deterministic sample selection.

## Exact-before-lossy rule

`search_reduce` is fail-closed unless `GatewayPatchProvider` has a `ToolOutputExternalizer` backed by exact evidence storage.

Before aggregation, ranking or sampling can be returned, the complete locally fetched and filtered candidate window is serialized and captured through the existing externalizer/evidence owner. The capture callback must return both an artifact id and an exact recovery handle, and the handle is re-verified before the reduction is accepted.

The raw candidate artifact is never ingested into provider-visible active context. The provider receives only:

- query/operator identity;
- compact reduced value;
- raw/filtered candidate counts;
- explicit `source_window_complete` state;
- exact local artifact/recovery handles.

Therefore hidden graph fields and oversized raw candidate rows remain local while exact recovery remains possible.

## Window completeness

The engine conservatively marks `source_window_complete=false` whenever the graph fills the bounded fetch window. Such a result is candidate-window evidence only and must not be represented as a global repository aggregate.

The system prompt explicitly communicates this boundary to the model.

## Invalidation

Each provider attempt computes a semantic invalidation fingerprint from:

- stable project identity;
- task identity;
- attempt number;
- current diff hash;
- changed-file set.

That fingerprint is attached to the exact raw reduction artifact so externalization dedupe/delta reuse cannot cross a changed attempt state silently.

## Security/capability policy

Projection and filters remain engine allow-listed. Reduction fields are independently allow-listed by operator. The provider cannot request arbitrary graph fields merely because aggregation exists.

The exact raw capture is local evidence and is not converted into provider-visible preview material. The compact reduction itself still goes through the normal active-context ingestion/externalization path.

## Regression proof

`tests/runtime/test_query_pushdown_gateway.py` proves:

1. a real gateway loop can request `search_reduce` and continue to a patch;
2. the raw candidate window is recoverable through the exact evidence handle;
3. a deliberately hidden raw candidate field exists in exact evidence but is absent from the next provider packet;
4. the compact aggregate and recovery handle are provider-visible;
5. `search_reduce` fails closed when no exact externalizer is configured.

Dedicated exact-head CI: `.github/workflows/query-pushdown-gateway.yml`.

## Claim boundary

This pass certifies gateway mechanics and provider-visible data-shape reduction only. It does not convert local byte/token estimates into a provider-billed savings claim. Provider superiority remains gated by paired provider-observed receipts.

## Exit state

After the dedicated exact-head gate and existing recovery/product regressions pass, TE-P0-08 may be admitted as production-integrated. The next canonical owner is TE-P0-11 Workflow Skill / Macro Compiler, while any failed broad regression remains higher priority than advancing the roadmap.
