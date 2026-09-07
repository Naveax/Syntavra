# Syntavra HyperEfficiency Master Roadmap V6

Status: **ADMITTED ROADMAP / RECONCILIATION REQUIRED**  
Admitted: **2026-09-07**  
Certified Python boundary preserved: **236–280**  
New canonical roadmap range: **CAP-0281..CAP-1648**  
Imported source range: **HE-0001..HE-1368**

## 1. Authority and merge rule

This is the append-only post-280 roadmap extension. It does not reopen or renumber certified work.

- 236–270 and 276–280 keep their existing certified authority.
- 271–275 remain deferred Rust-transition work.
- `rust_resume_allowed=false`, `rust_retired=true`.
- Rust production promotion remains 174/245 with 71 remaining.
- CAP-0281..CAP-1648 enter as `ADMITTED_RECONCILIATION`, not as proof that 1,368 implementations are missing.
- HE-1285..HE-1368 / CAP-1565..CAP-1648 are the admitted Token Elimination v10 extension.
- `docs/plans/SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md` is the current architecture/reconciliation overlay for context, retrieval, tool, schema, compression and inference-economy work. It creates no parallel capability namespace.
- Before implementation, every candidate is classified `EXISTS`, `HARDEN`, `UNIFY`, `NEW`, `CERTIFY`, `EXTERNAL` or `DEFERRED` against current canonical owners.
- Existing stores, ledgers, routers, indexes, registries and public surfaces are reused unless an explicit architecture decision proves a new owner is required.

## 2. Product identity

Syntavra's long-term role is a **Verified Experience-Compiling Inference Operating System**.

The governing objective is to minimize expensive provider inference required per **verified successful software-engineering task**, while preserving correctness, security, recoverability and external-evidence boundaries.

The near-term competitive core is the **Context Execution Compiler**: exclude, scope, deduplicate, retrieve, admit and externalize before compression; then route only residual uncertainty to the cheapest sufficient inference path.

### Primary KPI

`Verified Provider Cost / Successful Task`

### Supporting KPIs

- Provider Tokens / Verified Successful Task
- Frontier Dependency Ratio
- Inference-Free Task Fraction
- Repeated Work Reuse
- Repository Query Cache Hit
- Reacquisition Waste
- Unnecessary Tool Calls
- Duplicate Read / Command Waste
- MCP Schema Tokens / Turn
- Exact Recovery Coverage
- Cache Invalidation Correctness
- Verification Coverage

Engineering moonshots such as >=95% provider-cost reduction are workload-scoped targets, never public superiority claims without provider-observed receipts under frozen equivalent task/verifier conditions.

## 3. Canonical execution order

```text
UNDERSTAND
→ EXCLUDE IRRELEVANT STATE
→ REUSE EXPERIENCE / CACHE / ARTIFACTS
→ PREDICT
→ PROVE WHAT CHANGED AND WHAT IS NEEDED
→ ADMIT THE MINIMUM SUFFICIENT CONTEXT
→ SOLVE DETERMINISTICALLY / SYMBOLICALLY
→ VERIFY
→ LOCAL SPECIALIST
→ CHEAP MODEL
→ FRONTIER ONLY FOR RESIDUAL UNCERTAINTY
→ VERIFY AGAIN
→ LEARN AN ABSTRACTION
```

Compression is a late-stage optimization, not the first move. Deterministic exclusion, deduplication, cache reuse and admission happen first.

## 4. Capability lanes

### Lane A — Universal

Requirements, execution state, repository retrieval, evidence graphs, tool shaping, caching, context admission/virtualization, verification, memory/reuse, routing and work avoidance.

### Lane B — Provider-assisted

Only when exposed by the provider/host: prompt caching, cache breakpoints, native compaction, explicit reasoning budgets, deferred/dynamic tools, structured outputs and server-side state handles.

### Lane C — Self-hosted / open-weight

Optional aggressive layer: KV/prefix-state reuse, KV compression, speculative decoding, custom attention policies, continuous batching, persistent model state and hardware-aware serving.

Lane B/C capabilities must never silently become universal requirements.

### Lane D — External adapters / competitive references

Graphify, Serena, Aider Repo-Map, RTK, Ponytail, Caveman, Headroom, 9Router, LiteLLM and OpenRouter may be integrated or benchmarked, but they do not automatically become canonical runtime owners. Tree-sitter, LSP and ripgrep are low-level primitives. LLMLingua-family and TOON transforms remain fidelity/tokenizer-gated experiments.

## 5. Defensible system moats

1. **Repository Understanding** — persistent digital twin instead of rediscovery per task.
2. **Experience** — verified trajectories become reusable strategies, macros, skills and abstractions.
3. **Verification** — verifier-first execution plus verifier-of-verifier confidence.
4. **Amortization** — repeated calls, branches, queries, proofs and repository knowledge are reused.
5. **Inference Exception Architecture** — frontier inference becomes the exception path for residual uncertainty.
6. **Context Execution Compilation** — context is selected and compiled from provenance-aware state instead of replayed wholesale.
7. **Evidence-Preserving Reduction** — lossy views never destroy the exact artifact needed to recover or verify a result.

## 6. Development order

Capability number is identity, not implementation order.

### M6-0 — Reconcile before coding

For each candidate:

1. locate canonical owner;
2. classify `EXISTS/HARDEN/UNIFY/NEW/CERTIFY/EXTERNAL/DEFERRED`;
3. record dependency and invalidation edges;
4. define acceptance/verifier contract;
5. reject duplicate stores/routers/indexes/public surfaces;
6. only then derive implementation work.

The 48 architecture names in `SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md` are reconciled under this same process; they are not 48 automatically new capabilities.

### M6-1 — Measurement

Cost Ledger, Frontier Dependency Ratio, Reacquisition accounting, Waste accounting, provider-token attribution and token-per-success baselines.

### M6-2 — Correct state

Execution Ledger, Semantic State Machine, Requirements Compiler, provenance/TTL and invalidation.

### M6-3 — Correct success

Verifier Graph, Spec-Harness / Verifier-of-Verifier, mutation adequacy and work-equivalence contracts.

### M6-4 — Stop relearning

Hierarchical Experience Compiler, Strategy Memory, Plan Memory, Failure Ontology.

### M6-5 — Stop reading

Repository Digital Twin, Query Compiler, Program Slicing, Semantic Trace, generated/vendor exclusion, monorepo scope, content-addressed reads and token-cost-aware graph traversal.

### M6-6 — Stop replaying context and tools

Context Replay Breaker, Context Admission Controller, Artifact-First Tool Pipeline, duplicate read/command suppression, MCP schema governance, schema-on-demand discovery, scoped AGENTS.md compilation and typed checkpoint/handoff.

### M6-7 — Stop calling models

Inference Compiler, Inference Exception Architecture, deterministic workflow compiler, local specialists, reasoning-effort governance and frontier-last router.

### M6-8 — Make frontier calls compound

Frontier Knowledge Harvesting, Abstraction Discovery, Macro Factory, repository-specific distillation.

### M6-9 — Amortize

Hierarchical work/cache reuse, cross-task work coalescing, parallel work deduplication, branch/PR amortization, cross-repository transfer and provider-call amortization.

### M6-10 — Compress only the residual

Tokenizer-Aware Serializer, optional TOON, recoverable Headroom/Caveman-style adapter experiments and LLMLingua/LLMLingua-2/LongLLMLingua fidelity-gated lanes. No-expansion and exact-recovery gates are mandatory.

### M6-11 — Self-optimize safely

Adaptive Retrieval Learning, Token-per-Success Optimizer, counterfactual replay, offline policy evaluation, shadow deployment, workload/repository-specific promotion and rollback on regression.

### M6-12 — Advanced serving/provider compilation

Provider-assisted and self-hosted optimizations remain capability-gated and isolated from the universal core, including Native Codex Compaction cooperation when explicitly exposed.

### M6-13 — Certified moonshot

A moonshot mode is enabled only for workload classes that pass frozen-task, same-verifier, solve-rate, security and provider-receipt gates.

## 7. Fixed first reconciliation slice

The first pass is these 27 system families, in order:

1. Cost Ledger
2. Frontier Dependency Ratio
3. Reacquisition accounting
4. Waste accounting
5. Execution Ledger
6. Semantic State Machine
7. Requirements Compiler
8. Verifier Graph
9. Spec-Harness / verifier-of-verifier
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

The Context Execution Compiler overlay must map into these canonical families and the Token Elimination v10 rows before implementation work is created.

## 8. Context Execution Compiler waves

Execution detail lives in `docs/plans/SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md`:

```text
CX-0 reconcile + baseline
CX-1 zero-risk waste elimination
CX-2 repository intelligence fabric
CX-3 artifact-first tools + tests
CX-4 MCP + instruction compilation
CX-5 replay/state/lifecycle
CX-6 serialization + recoverable compression
CX-7 inference economy
CX-8 adaptive optimization
```

Global ordering invariant:

`exclude → scope → deduplicate → retrieve/select → externalize → serialize/compress → route → verify → learn`

## 9. Promotion gate

Every implementation-bearing candidate follows:

```text
requirement/spec
→ canonical owner
→ minimal mutation scope
→ verifier plan
→ implementation
→ targeted verification
→ regression/security verification
→ cost/latency receipt where relevant
→ exact recovery/provenance where relevant
→ state update
```

Important behavior must include verifier-confidence testing. A verified cheap/local result does not escalate to frontier merely out of habit. Frontier output is still a candidate until independently verified. Repeated successful frontier work should be harvested into reusable strategy or abstraction state.

Context-reduction changes additionally require:

```text
provenance + invalidation proof
→ target-tokenizer measurement
→ no-expansion gate
→ fidelity/recovery verifier
→ shadow/counterfactual comparison where policy changes
→ rollback path
```

## 10. Canonical mapping and catalog

```text
canonical_capability = 280 + HE_source_id

HE-0001 → CAP-0281
HE-0339 → CAP-0619
HE-0340 → CAP-0620
HE-0654 → CAP-0934
HE-0655 → CAP-0935
HE-0982 → CAP-1262
HE-0983 → CAP-1263
HE-1284 → CAP-1564
HE-1285 → CAP-1565
HE-1368 → CAP-1648
```

The complete 1,368-row catalog is stored in nine bounded machine-readable shards:

- `contracts/python/hyperefficiency/roadmap-v1-v3a.json`
- `contracts/python/hyperefficiency/roadmap-v1-v3b.json`
- `contracts/python/hyperefficiency/roadmap-v1-v4a.json`
- `contracts/python/hyperefficiency/roadmap-v1-v4b.json`
- `contracts/python/hyperefficiency/roadmap-v1-v5a.json`
- `contracts/python/hyperefficiency/roadmap-v1-v5b.json`
- `contracts/python/hyperefficiency/roadmap-v1-v6a.json`
- `contracts/python/hyperefficiency/roadmap-v1-v6b.json`
- `contracts/python/hyperefficiency/roadmap-v1-token-elimination-v10.json`

Each row preserves source number, canonical number, exact capability title, generation and source section. `contracts/python/hyperefficiency-roadmap-v1.json` binds ranges, counts and SHA-256 identities.

## 11. Source lineage and provenance

The admitted plan combines the original HyperEfficiency design corpus and Token Elimination v10:

- two precursor HyperEfficiency/context-efficiency design inputs;
- V3 numbered roadmap HE-0001..HE-0339;
- V4 numbered roadmap HE-0340..HE-0654;
- V5 numbered roadmap HE-0655..HE-0982;
- V6 numbered roadmap HE-0983..HE-1284;
- Token Elimination v10 HE-1285..HE-1368.

Their SHA-256 fingerprints are retained in the root roadmap registry. The canonical execution authority is the root registry plus the nine bounded shards; raw source prose is not duplicated into the repository merely as archival ballast.

The Context Execution Compiler document is an architecture/reconciliation overlay derived from current competitive research and the admitted corpus. It does not renumber or silently append capabilities.

## 12. CI and evidence discipline

- Never dispatch or rerun an equivalent workflow for the same SHA/workflow/input while it is queued or in progress.
- Track the existing `run_id` and continue independent work while CI runs.
- Preserve exact-head certification semantics.
- Do not manufacture provider receipts, independent validation, publication evidence or adoption from repository-local tests.
- Rust transition stays blocked until separate explicit reactivation authority exists.
- External project benchmark numbers remain external evidence until reproduced under frozen Syntavra workloads.

## 13. Completion rule

The admitted roadmap is absorbed only when all **1,368** candidates have a reconciliation classification and every implementation-bearing item has a canonical owner, dependency/invalidation edges, verifier and promotion gate. Candidate count itself is not a progress metric.

The Context Execution Compiler overlay is complete only when all 48 names have a recorded reconciliation result and every promoted transform has target-tokenizer, fidelity, recovery/invalidation and token-per-success evidence appropriate to its class.

> Solve once, verify it, compile the experience into reusable machinery, and make the next equivalent task cheaper or inference-free.
