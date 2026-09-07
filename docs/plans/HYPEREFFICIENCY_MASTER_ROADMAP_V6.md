# Syntavra HyperEfficiency Master Roadmap V6

Status: **ADMITTED ROADMAP / RECONCILIATION REQUIRED**  
Admitted: **2026-09-07**  
Existing certified Python capability boundary: **236–280**  
New canonical roadmap range: **281–1564**  
Imported HyperEfficiency source range: **HE-0001–HE-1284**

## 1. Authority and non-destructive merge rule

This document is the append-only post-280 roadmap extension. It does not reopen, renumber or invalidate previously certified work.

- Capabilities 236–270 and 276–280 keep their existing certified state.
- Capabilities 271–275 remain deferred Rust-transition work.
- `rust_resume_allowed=false` and `rust_retired=true` remain unchanged.
- New items 281–1564 are admitted as roadmap candidates with state `ADMITTED_RECONCILIATION`, **not** as proof that 1,284 implementations are missing.
- Before any implementation, every candidate must be reconciled against current canonical owners and classified `EXISTS`, `HARDEN`, `UNIFY`, `NEW`, `CERTIFY`, `EXTERNAL` or `DEFERRED`.
- Existing stores, ledgers, routers, indexes, registries, public commands and security boundaries must be reused unless an explicit architecture decision proves a new owner is required.

This prevents the roadmap from becoming 1,284 chances to implement the same subsystem twice.

## 2. Product identity

Syntavra's long-term identity is:

> **Verified Experience-Compiling Inference Operating System**

The objective is not merely to compress tokens. The objective is to minimize expensive provider inference required to complete a **verified** software-engineering task while preserving correctness, security, recoverability and external-evidence boundaries.

### Primary KPI

```text
Verified Provider Cost / Successful Task
```

### Supporting KPIs

```text
Frontier Dependency Ratio
Inference-Free Task Fraction
Repeated Work Reuse
Repository Query Cache Hit
Reacquisition Waste
Unnecessary Tool Calls
Verification Coverage
```

Moonshot targets are engineering targets, not product claims. Provider-billed superiority remains externally evidence-gated.

### Certified-routine engineering targets

```text
Frontier Dependency Ratio       < 5%
Inference-Free Task Fraction    > 50–70%
Repeated Work Reuse             > 90%
Repository Query Cache Hit      > 80%
Reacquisition Waste             < 10%
Unnecessary Tool Calls          < 5%
Verification Coverage           ~100%
Provider Cost Reduction         >=95% moonshot
```

These values are workload-scoped engineering targets. They are never converted into a public claim without the repository's provider-observed evidence gates.

## 3. Canonical execution order

```text
UNDERSTAND
  ↓
REUSE EXPERIENCE
  ↓
PREDICT
  ↓
PROVE WHAT IS NEEDED
  ↓
SOLVE SYMBOLICALLY / DETERMINISTICALLY
  ↓
VERIFY
  ↓
USE LOCAL SPECIALIST
  ↓
USE CHEAP MODEL
  ↓
USE FRONTIER ONLY FOR RESIDUAL UNCERTAINTY
  ↓
VERIFY AGAIN
  ↓
LEARN AN ABSTRACTION
```

Compression is a late-stage optimization, not the first move.

## 4. Capability lanes

### Lane A — Universal

Applicable across hosted coding agents and providers: requirements, execution ledger, repository retrieval, evidence graph, tool shaping, caching, context virtualization, verification, memory/reuse, routing and work avoidance.

### Lane B — Provider-assisted

Used only when the provider exposes a compatible capability: prompt caching, cache breakpoints, native compaction, explicit reasoning budgets, deferred/dynamic tools, structured outputs and server-side state handles.

### Lane C — Self-hosted / open-weight

Optional aggressive layer: KV/prefix-state reuse, KV compression, speculative decoding, custom attention policies, continuous batching, persistent model state and hardware-aware serving.

A Lane B/C capability must never silently become a universal product requirement.

## 5. Five defensible moats

1. **Repository Understanding** — persistent digital twin instead of rediscovering the repository per task.
2. **Experience** — successful trajectories become reusable strategies, macros, skills and abstractions.
3. **Verification** — verifier-first execution plus verifier-of-verifier confidence.
4. **Amortization** — repeated work, calls, branches, queries, proofs and repository knowledge are reused.
5. **Inference Exception Architecture** — frontier inference becomes an exception path for unresolved uncertainty.

## 6. Development order

Capability number is an identity, **not** implementation order.

### M6-0 — Reconcile before coding

For all candidates, beginning with the P0 slice:

1. locate canonical owner,
2. classify `EXISTS/HARDEN/UNIFY/NEW/CERTIFY/EXTERNAL/DEFERRED`,
3. record dependency and invalidation edges,
4. define acceptance/verifier contract,
5. reject duplicate stores/routers/indexes/public surfaces,
6. only then create an implementation task.

### M6-1 — Measurement

- Cost Ledger
- Frontier Dependency Ratio
- Reacquisition accounting
- Waste accounting

Gate: no optimization is promoted if its savings source cannot be attributed.

### M6-2 — Correct state

- Execution Ledger
- Semantic State Machine
- Requirements Compiler

### M6-3 — Correct success

- Verifier Graph
- Spec-Harness / Verifier-of-Verifier
- Mutation adequacy

### M6-4 — Stop relearning

- Hierarchical Experience Compiler
- Strategy Memory
- Plan Memory
- Failure Ontology

### M6-5 — Stop reading

- Repository Digital Twin
- Query Compiler
- Program Slicing
- Semantic Trace

### M6-6 — Stop calling models

- Inference Compiler
- Inference Exception Architecture
- Deterministic workflow compiler
- Local specialists
- Frontier-last router

### M6-7 — Make frontier calls compound

- Frontier Knowledge Harvesting
- Abstraction Discovery
- Macro Factory
- Repository-specific distillation

### M6-8 — Amortize

- hierarchical cache/work cache
- cross-task work coalescing
- branch/PR amortization
- cross-repository transfer
- provider-call amortization

### M6-9 — Self-optimize safely

- counterfactual replay
- offline policy evaluation
- shadow deployment
- workload/repository-specific policy promotion
- rollback on regression

### M6-10 — Advanced serving/provider compilation

Provider-assisted and self-hosted optimizations remain capability-gated and must not contaminate the universal core.

### M6-11 — Certified moonshot

`>=95%` mode is enabled only for a workload class that passes the same frozen-task, same-verifier, solve-rate, security and provider-receipt gates.

## 7. First P0 reconciliation slice

The first reconciliation pass is fixed to these 27 system families:

1. Cost Ledger
2. Frontier Dependency Ratio
3. Reacquisition accounting
4. Waste accounting
5. Execution Ledger
6. Semantic State Machine
7. Requirements Compiler
8. Verifier Graph
9. Spec-Harness
10. Mutation adequacy
11. Hierarchical Experience Compiler
12. Strategy Memory
13. Plan Memory
14. Failure Ontology
15. Repository Digital Twin
16. Query Compiler
17. Program Slicing
18. Semantic Trace
19. Inference Compiler
20. Inference Exception Architecture
21. Deterministic workflow compiler
22. Local specialists
23. Frontier-last router
24. Frontier Knowledge Harvesting
25. Abstraction Discovery
26. Macro Factory
27. Repository-specific distillation

This slice takes priority over simply walking `CAP-0281 → CAP-1564`.

## 8. Verification and promotion gates

Every implementation-bearing candidate needs:

```text
requirement/spec
→ canonical owner
→ minimal mutation scope
→ deterministic/probabilistic verifier plan
→ implementation
→ targeted verification
→ regression/security verification
→ cost/latency receipt where relevant
→ exact recovery/provenance where relevant
→ state update
```

Additional rules:

- Verifier confidence must itself be tested for important behavior.
- High-risk evidence stays exact or strongly recoverable.
- A local/cheap model result that passes the required verifier must not escalate to frontier merely out of habit.
- A frontier answer is still a candidate until independently verified.
- Repeated successful frontier work should be harvested into reusable strategy/abstraction/skill state.

## 9. CI and repository discipline

- Never dispatch or rerun an equivalent workflow for the same SHA/workflow/input while it is queued or in progress.
- Track the existing `run_id`; continue independent work while CI runs.
- Preserve exact-head certification semantics.
- Do not manufacture provider receipts, independent validation, publication evidence or adoption from repository-local tests.
- Rust transition stays blocked until separate explicit reactivation authority exists.

## 10. Source lineage

The merge is intentionally lossless at the planning level:

- precursor context-efficiency design input,
- precursor extreme-optimization design input,
- V3 numbered roadmap: HE-0001–HE-0339,
- V4 numbered roadmap: HE-0340–HE-0654,
- V5 numbered roadmap: HE-0655–HE-0982,
- V6 numbered roadmap: HE-0983–HE-1284.

Detailed raw source material is retained as six provenance snapshots under `docs/research/hyperefficiency/`.

Machine-readable identity/state mapping is retained in `contracts/python/hyperefficiency-roadmap-v1.json`.

## 11. Canonical capability mapping

Canonical mapping is deterministic:

```text
canonical_capability = 280 + HE_source_id
HE-0001  → CAP-0281
HE-0339  → CAP-0619
HE-0340  → CAP-0620
HE-0654  → CAP-0934
HE-0655  → CAP-0935
HE-0982  → CAP-1262
HE-0983  → CAP-1263
HE-1284  → CAP-1564
```

The bounded catalogs preserve every numbered roadmap title and its originating program/system. Detailed rationale remains in the source snapshots.

## 13. Canonical imported catalog

The 1,284 admitted identities are stored in bounded human-readable catalogs and mirrored by the machine-readable registry. This keeps continuation and reconciliation bounded instead of forcing every agent to ingest 1,284 rows to answer one question.

- `docs/plans/hyperefficiency/CATALOG_V3.md` — HE-0001..0339 / CAP-0281..0619
- `docs/plans/hyperefficiency/CATALOG_V4.md` — HE-0340..0654 / CAP-0620..0934
- `docs/plans/hyperefficiency/CATALOG_V5.md` — HE-0655..0982 / CAP-0935..1262
- `docs/plans/hyperefficiency/CATALOG_V6.md` — HE-0983..1284 / CAP-1263..1564

Machine-readable authority:

- `contracts/python/hyperefficiency-roadmap-v1.json` — canonical index, ranges and shard hashes
- `contracts/python/hyperefficiency/roadmap-v1-v3a.json` ... `roadmap-v1-v6b.json` — bounded capability rows

## 14. Lossless source provenance

The six supplied design inputs are retained under `docs/research/hyperefficiency/` as `precursor-a.md`, `precursor-b.md`, `v3.md`, `v4.md`, `v5.md` and `v6.md`. Their rationale is provenance; this roadmap and the machine-readable registry control canonical identity/state.

## 15. Completion rule

The roadmap is successfully absorbed only when all 1,284 candidates have a reconciliation classification and every implementation-bearing item has a canonical owner, dependency/invalidation edges, verifier and promotion gate. Capability count is not a progress metric.

> Solve once, verify it, compile the experience into reusable machinery, and make the next equivalent task cheaper or inference-free.
