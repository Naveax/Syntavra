# Syntavra Current Token-Floor Checkpoint

Updated: **2026-09-08**  
Status: ACTIVE VOLATILE CONTINUATION OVERLAY  
No new CAP namespace.

## Current authority

For token-floor work, read the existing HyperEfficiency/Context Execution Compiler authorities first, then continue through the append-only token-economy lineage. The newest execution authorities are:

1. `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V6.md`
2. `docs/plans/SYNTAVRA_ULTRA_LOW_TOKEN_FRONTIER_V5.md`
3. `contracts/python/token-economy-execution-backlog-v6.json`
4. `contracts/python/ultra-low-token-frontier-v5.json`
5. Previous V1..V5 backlog and V1..V4 frontier files remain prerequisites/lineage, not superseded evidence.

## Current floor-search objective

`minimum verified provider work = irreducible information + irreducible uncertainty`

Verified Token Floor Search remains continuous. A certified band is not a permanent floor; new mechanisms trigger another offline/shadow downward probe.

Current V5 research probes, not product claims:

```text
verified repeated deterministic: 0 provider tokens
easy/warm eligible:             0-25
easy frontier-needed:           25-150
normal scoped coding:           150-750
hard scoped coding:             750-2,500 before necessity-backed overflow
```

A workload may have a higher safe floor. Quality/verifier/security/exact-recovery always outrank the target number.

## Deterministic foundation progress

### TE-P0-01 Pre-Model Ingestion Fold Gate

Exact-head CI admitted on the active branch.

Canonical implementation note:

- `docs/TE_P0_01_PRE_MODEL_INGESTION_FOLD_GATE.md`

Runtime owner hardened:

- `syntavra_runtime/agent_context_runtime.py`

Dedicated regression coverage:

- `tests/runtime/test_pre_model_ingestion_fold_gate.py`

Implemented boundary:

- exact raw persistence before provider admission when the exact externalizer is active;
- first-visibility fold attribution;
- larger bounded mandatory-failure evidence lease;
- mandatory failure/security/verifier evidence cannot be silently preview-dropped to fit a provider budget;
- scope-bound exact recall with a hard byte cap;
- untrusted-data guard on recalled raw evidence;
- deterministic repeated-recall accounting and capped warm leases;
- unchanged-result zero-preview attribution.

### TE-P0-02 Active Context Supersession Graph

Exact-head deterministic-foundation CI admitted on the active branch.

Canonical implementation note:

- `docs/TE_P0_02_ACTIVE_CONTEXT_SUPERSESSION_GRAPH.md`

Dedicated regression coverage:

- `tests/runtime/test_active_context_supersession_graph.py`

Implemented boundary:

- logical evidence streams are derived conservatively from actual view identity rather than caller keys;
- complete read/search/diff/impact/verifier views can supersede prior versions;
- incomplete search/query-shape metadata disables cross-generation supersession;
- recoverable stale bodies collapse to `SUPERSEDED_TO_HANDLE` receipts;
- mandatory failure/security/verifier evidence remains pinned;
- unrecoverable evidence remains pinned;
- invalidation fingerprints are attributed without being abused as equivalence;
- stale exact-recall bodies are removed when their canonical stream advances;
- capacity pressure never evicts the newest canonical state merely because older pinned evidence exists;
- compile-time provider budgets remain fail-closed.

### TE-P0-03 Useless / No-Change Result Elision

Structural implementation is admitted on the active branch.

Canonical implementation note:

- `docs/TE_P0_03_NO_CHANGE_RESULT_ELISION.md`

Runtime owner:

- `syntavra_runtime/agent_context_runtime.py`

Regression coverage includes:

- `tests/runtime/test_pre_model_ingestion_fold_gate.py`
- `tests/runtime/test_token_economy_rc1_runtime.py`

Implemented boundary:

- zero-preview `UNCHANGED` receipts require stable logical-view identity;
- content SHA-256 and semantic metadata must both match;
- caller-key drift does not defeat proven logical-view equivalence;
- metadata/invalidation drift refuses elision;
- prior evidence must still be active or exactly recoverable;
- incomplete search/query shape fails closed rather than inventing cross-generation equivalence.

### TE-P0-04 Causal History Skeleton / Constant-Context Tool Loop

Implementation candidate is present and requires exact-head admission with the dedicated long-session regression.

Canonical implementation note:

- `docs/TE_P0_04_CAUSAL_HISTORY_SKELETON.md`

Dedicated regression:

- `tests/runtime/test_causal_history_skeleton.py`

Reconciliation classification:

`EXISTS + HARDEN + CERTIFY`

Candidate boundary:

- active evidence is separate from bounded causal receipts;
- recoverable stale bodies become handle-only lineage;
- causal receipts do not replay raw tool bodies;
- unrecoverable or mandatory evidence remains pinned;
- long-session compiled context is tested against a 5x round-count increase while active streams and causal receipt caps stay fixed;
- exact handles remain required before body eviction.

Still open before the wider TE-P0-04 family is globally complete:

- reconcile current-diff hunk/symbol representation with existing diff owners;
- reconcile successful verifier-log collapse with existing verifier/externalization owners;
- prove no host/provider adapter bypasses the constant-context contract;
- obtain paired provider-observed evidence before making savings claims.

Dedicated exact-head workflow for TE-P0-01..04:

- `.github/workflows/token-economy-deterministic-foundations.yml`

Provider-savings claims remain closed until paired provider-observed evidence passes the existing proof gates.

## CI reconciliation note

A stale recovery regression previously expected unrecoverable evidence to be evicted merely to satisfy `max_active`. That contradicted the admitted exact-recovery invariant and has been corrected to exercise handle-backed evidence instead.

The RC1 structural benchmark has likewise been moved onto an exact `EvidenceStore` + `ToolOutputExternalizer` fixture so constant-context eviction is measured only when exact recovery exists. These fixes do not weaken fail-closed behavior and do not create provider-savings claims.

## Existing prerequisite owners to reconcile, not duplicate

Before writing parallel implementations, inspect the current owners:

- TE-P0-08 Query Pushdown: `syntavra_runtime/agent_retrieval.py` already provides bounded projection/filter/row limiting and fused search-inspect behavior. Remaining backlog operators and policy coverage must be reconciled against that owner.
- TE-P0-10 Verifier-Gated Inference Skip: `syntavra_runtime/inference_skip_cache.py` already has exact replay/invalidation machinery and recovery CI coverage. Remaining backlog requirements must be reconciled before declaring the whole item complete.

Within the TE-1 wave, TE-P0-09 Delta Tool Response Protocol is the next reconciliation target after TE-P0-04 admission. Existing baseline/delta/externalization behavior must be classified before adding new machinery.

## Current V5 continuation: TE-U30..U36

1. `U5-P0-01` Zero-Token Memory Engine.
2. `U5-P0-02` Reacquisition Tax Governor.
3. `U5-P0-03` Future-Reuse Retention Predictor.
4. `U5-P0-04` Communication Medium Router.
5. `U5-P0-05` Global Token Budget Auction.
6. `U5-P1-03` Reacquisition-Aware Compaction Scheduler.
7. `U5-P1-01` Entropy-Coded SWIR V2.
8. `U5-P1-02` Reversible Source Minification, experimental only.
9. `TE-U36` compound provider-receipt/floor certification and next downward probe.

These do not outrank unfinished deterministic foundations and earlier P0 work: constant-context history, delta response protocol, query-pushdown completion, verifier-gated inference-skip reconciliation, macro execution, tool-chain fusion, AST-native edit, adaptive retrieval, early-stop/retry governance, strategic silence/selective escalation, data-layer reduction and verified token-floor search.

## V5 optimization rule

`avoid LLM memory work -> account for future reacquisition -> retain only when reuse economics win -> choose the cheapest faithful communication medium -> allocate scarce tokens by marginal verified value -> entropy-code residual wire forms -> optionally reversible-minify source -> verify -> search lower again`

## Measurement/claim boundary

- memory maintenance provider tokens default to zero;
- all reacquisition/re-read/search/tool tokens caused by prior pruning count end-to-end;
- all recovery/retry/repair/fallback/speculative calls count end-to-end;
- local KV/latent/decode acceleration is not provider-token savings by itself;
- provider savings require paired provider-observed receipts under equivalent frozen tasks/verifiers;
- no component percentage is added arithmetically to another component percentage;
- mandatory user/security/failure/verifier evidence cannot be silenced, dropped, auctioned away or learned out;
- reversible source minification stays experimental until exact round-trip and non-inferior verifier evidence exist.

## Continuous-lowering rule

After V5 certification, floor search may probe below `25`, `150`, `750` and `2,500` again. Stop only when the next reduction worsens verified solve rate, security/constraint compliance, exact recovery, provider cost per successful task, or retry/recall/reacquisition/fallback burden.

## Preserved project authority

`PYTHON_COMPLETE(v1)=true`; Python 236-270 and 276-280 remain closed/certified; 271-275 remain deferred Rust-transition work; `rust_resume_allowed=false`; `rust_retired=true`; Rust production promotion remains 174/245 with 71 remaining.

## Next session

1. Require exact-head admission of `TE-P0-04 Causal History Skeleton`.
2. Reconcile `TE-P0-09 Delta Tool Response Protocol` against existing baseline/externalization owners before implementing anything new.
3. Reconcile the already-present Query Pushdown and Inference Skip owners against the remaining P0-08/P0-10 checklist.
4. Continue deterministic prerequisites in execution-backlog order.
5. Only after those prerequisites are admitted, begin the V5 Zero-Token Memory Engine and Reacquisition Tax Governor lane.
