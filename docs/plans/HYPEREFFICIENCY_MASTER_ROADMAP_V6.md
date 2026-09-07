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
- `docs/plans/SYNTAVRA_TOKEN_ECONOMY_COMPETITIVE_GAP_V1.md` records competitive/research-derived token-economy gaps without creating new CAP identities.
- `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` is the operational TODO/continuation order for those gaps.
- `contracts/python/token-economy-execution-backlog-v1.json` is the machine-readable execution/status mirror.
- Before implementation, every candidate is classified `EXISTS`, `HARDEN`, `UNIFY`, `NEW`, `CERTIFY`, `EXTERNAL` or `DEFERRED` against current canonical owners.
- Existing stores, ledgers, routers, indexes, registries and public surfaces are reused unless an explicit architecture decision proves a new owner is required.

## 2. Product identity

Syntavra's long-term role is a **Verified Experience-Compiling Inference Operating System**.

The governing objective is to minimize expensive provider inference required per **verified successful software-engineering task**, while preserving correctness, security, recoverability and external-evidence boundaries.

The near-term competitive core is the **Context Execution Compiler**: exclude, scope, deduplicate, retrieve, admit and externalize before compression; then route only residual uncertainty to the cheapest sufficient inference path.

The token-economy rule is stronger than “compress context”:

`do not create -> do not retrieve -> do not resend -> do not reason again -> do not generate -> compact only the irreducible residual`

For repeated deterministic tasks with complete repository/environment/policy/verifier fingerprints, the target path may bypass provider inference entirely.

### Primary KPI

`Verified Provider Cost / Successful Task`

### Supporting KPIs

- Provider Tokens / Verified Successful Task
- Provider Tokens / New Verified Information
- Frontier Dependency Ratio
- Inference-Free Task Fraction
- Repeated Work Reuse
- Repository Query Cache Hit
- Reacquisition Waste
- Unnecessary Tool Calls
- Duplicate Read / Command Waste
- MCP Schema Tokens / Turn
- Tool Output Tokens / Turn
- Active vs Superseded Evidence Tokens
- Reasoning Tokens / Successful Task
- Prompt Cache Break Count / Cause
- Inference-Skip Hit Rate / Prevented False Hits
- Exact Recovery Coverage
- Cache Invalidation Correctness
- Verification Coverage

Engineering moonshots such as >=95% provider-cost reduction are workload-scoped targets, never public superiority claims without provider-observed receipts under frozen equivalent task/verifier conditions.

## 3. Canonical execution order

```text
UNDERSTAND
→ DO NOT CREATE UNNECESSARY STATE
→ EXCLUDE IRRELEVANT STATE
→ SCOPE
→ DEDUPLICATE / SUPERSEDE
→ REUSE EXPERIENCE / CACHE / ARTIFACTS
→ PREDICT
→ PROVE WHAT CHANGED AND WHAT IS NEEDED
→ RETRIEVE ONLY REQUIRED EVIDENCE
→ EXTERNALIZE RECOVERABLE BULK
→ PROJECT / AGGREGATE / DELTA
→ ADMIT THE MINIMUM SUFFICIENT CONTEXT
→ SOLVE DETERMINISTICALLY / SYMBOLICALLY
→ VERIFY
→ SKIP INFERENCE WHEN A COMPLETE VERIFIED FINGERPRINT MATCHES
→ LOCAL SPECIALIST
→ CHEAP MODEL
→ FRONTIER ONLY FOR RESIDUAL UNCERTAINTY
→ VERIFY AGAIN
→ LEARN / COMPILE AN ABSTRACTION OR MACRO
→ COMPRESS ONLY THE IRREDUCIBLE RESIDUAL
```

Compression is a late-stage optimization, not the first move. Deterministic exclusion, deduplication, supersession, cache reuse, admission, projection and externalization happen first.

## 4. Capability lanes

### Lane A — Universal

Requirements, execution state, repository retrieval, evidence graphs, tool shaping, caching, context admission/virtualization, verification, memory/reuse, routing and work avoidance.

### Lane B — Provider-assisted

Only when exposed by the provider/host: prompt caching, cache breakpoints/diagnostics, native compaction/context editing, explicit reasoning budgets, deferred/dynamic tools, structured outputs and server-side state handles.

### Lane C — Self-hosted / open-weight

Optional aggressive layer: KV/prefix-state reuse, resource-wise KV caching, KV compression, speculative decoding, custom attention policies, continuous batching, persistent model state, hidden-state code pruning, learned reasoning pruning and hardware-aware serving.

Lane B/C capabilities must never silently become universal requirements.

### Lane D — External adapters / competitive references

Graphify, Serena, Aider Repo-Map, RTK, Ponytail, Caveman, Headroom, 9Router, LiteLLM and OpenRouter may be integrated or benchmarked, but they do not automatically become canonical runtime owners. Tree-sitter, LSP and ripgrep are low-level primitives. LLMLingua-family and TOON transforms remain fidelity/tokenizer-gated experiments.

Additional narrow competitive references include Qwen Code prompt-prefix stability, Anthropic Tool Search/context editing, PayPal SCOUT, context-fold, JetBrains observation masking, SWE-Pruner, AutoCompact/SelfCompact, Mem0, LightMem reproduction, GPTCache, Sketch-of-Thought and TokenSkip. Borrow mechanisms, not unnecessary dependencies.

## 5. Defensible system moats

1. **Repository Understanding** — persistent digital twin instead of rediscovery per task.
2. **Experience** — verified trajectories become reusable strategies, macros, skills and abstractions.
3. **Verification** — verifier-first execution plus verifier-of-verifier confidence.
4. **Amortization** — repeated calls, branches, queries, proofs and repository knowledge are reused.
5. **Inference Exception Architecture** — frontier inference becomes the exception path for residual uncertainty.
6. **Context Execution Compilation** — context is selected and compiled from provenance-aware state instead of replayed wholesale.
7. **Evidence-Preserving Reduction** — lossy views never destroy the exact artifact needed to recover or verify a result.
8. **Pre-Provider Work Elimination** — oversized results, stale history, unchanged data and unnecessary schemas are prevented from becoming provider context at all.
9. **Verified Zero-Inference Reuse** — complete task/state/policy/verifier fingerprints can turn repeated safe workflows into local verified execution.
10. **Constant-Context Trajectories** — active provider context is bounded by live evidence rather than growing with every tool round.

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

The 48 architecture names in `SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md` and the competitive token-economy backlog are reconciled under this same process; they are not automatically new capabilities.

### M6-1 — Measurement

Cost Ledger, Frontier Dependency Ratio, Reacquisition accounting, Waste accounting, provider-token attribution and token-per-success baselines.

Token-economy attribution must separately explain fresh/cached input, repository/context, tool schema, active tool output, stale/superseded evidence, reasoning, answer, recalls, cache breaks, inference-skip events and provider cost. Paired traces should explain >=95% of provider-visible token mass before aggressive policy promotion.

### M6-2 — Correct state

Execution Ledger, Semantic State Machine, Requirements Compiler, provenance/TTL and invalidation.

### M6-3 — Correct success

Verifier Graph, Spec-Harness / Verifier-of-Verifier, mutation adequacy and work-equivalence contracts.

### M6-4 — Stop relearning

Hierarchical Experience Compiler, Strategy Memory, Plan Memory, Failure Ontology, raw+constructed dual-track memory and bounded multi-signal reranking.

### M6-5 — Stop reading

Repository Digital Twin, Query Compiler, Program Slicing, Semantic Trace, generated/vendor exclusion, monorepo scope, content-addressed reads, token-cost-aware graph traversal and optional task-aware residual line pruning after exact structural selection.

### M6-6 — Stop replaying context and tools

Context Replay Breaker, Context Admission Controller, Artifact-First Tool Pipeline, duplicate read/command suppression, MCP schema governance, schema-on-demand discovery, scoped AGENTS.md compilation and typed checkpoint/handoff.

The competitive hardening order inside this lane is:

1. Pre-Model Ingestion Fold Gate.
2. Active Context Supersession Graph.
3. Useless / No-Change Result Elision.
4. Causal History Skeleton + Constant-Context Tool Loop.
5. Delta Tool Response Protocol.
6. Stable Tool Schema Canonicalization.
7. Prompt Cache Break Detector.
8. Cache Break-Even Eviction Governor.
9. Tool Result Query Pushdown.
10. Hybrid Tool Search + Multi-Level Tool Detail.
11. Provider-native context editing/tool search where capability-gated and benchmark-superior.
12. OpenAI/provider cache adapters modernized without contaminating legacy compatibility paths.

Deterministic reduction order is:

`drop -> dedup -> supersede -> mask -> handle -> delta -> deterministic summary -> LLM summary`

Paid summarization is a last resort, not the first compaction button available.

### M6-7 — Stop calling models

Inference Compiler, Inference Exception Architecture, deterministic workflow compiler, verifier-gated inference skip cache, workflow skill/macro compiler, local specialists, reasoning-effort/reasoning-sketch governance and frontier-last router.

Exact verified reuse may bypass provider inference. Semantic similarity alone never authorizes a coding-agent inference skip.

### M6-8 — Make frontier calls compound

Frontier Knowledge Harvesting, Abstraction Discovery, Macro Factory, repository-specific distillation. Repeated verified tool graphs should become parameterized skills/macros so future tasks pay less planning cost.

### M6-9 — Amortize

Hierarchical work/cache reuse, cross-task work coalescing, parallel work deduplication, branch/PR amortization, cross-repository transfer, provider-call amortization and provider-prefix stability.

### M6-10 — Compress only the residual

Tokenizer-Aware Serializer, Semantic Wire IR, dynamic output contracts, semantic-novelty enforcement, optional TOON, recoverable Headroom/Caveman-style adapter experiments and LLMLingua/LLMLingua-2/LongLLMLingua fidelity-gated lanes. No-expansion and exact-recovery gates are mandatory.

Output savings count only when provider generation is prevented or constrained before generation. Post-generation truncation is not provider-token savings.

### M6-11 — Self-optimize safely

Adaptive Retrieval Learning, Token-per-Success Optimizer, counterfactual replay, offline policy evaluation, shadow deployment, workload/repository-specific promotion, phase/trajectory-aware compaction and rollback on regression.

### M6-12 — Advanced serving/provider compilation

Provider-assisted and self-hosted optimizations remain capability-gated and isolated from the universal core, including Native Codex Compaction cooperation when explicitly exposed, resource-wise KV caching, hidden-state pruning and learned reasoning-pruning only for owned/open models.

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

The Context Execution Compiler overlay and Token Economy Execution Backlog must map into these canonical families and the Token Elimination v10 rows before implementation work is created.

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

`exclude → scope → deduplicate → retrieve/select → externalize → project → serialize/compile → optional fidelity-gated compress → route → verify → learn`

## 8A. Competitive Token Economy execution waves

Operational detail lives in `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` and `contracts/python/token-economy-execution-backlog-v1.json`:

```text
TE-0 measurement + reconciliation
TE-1 ingestion + constant-context loop
TE-2 prefix/cache stability
TE-3 tool-data minimization
TE-4 inference elimination
TE-5 repository/memory residual minimization
TE-6 reasoning/output minimization
TE-7 local/open-weight experiments
TE-8 provider-receipt end-to-end certification
```

Current implementation evidence:

- `TE-P0-05 Stable Tool Schema Canonicalization` has a first hardening implementation at commit `8d78cfc0b66d01eea5f01b1aec0ad255ef5b692d`.
- Provider-observed cache savings for that mechanism remain uncertified until paired receipts exist.

Continuation order after TE-0 reconciliation is:

1. TE-P0-01 Pre-Model Ingestion Fold Gate.
2. TE-P0-02/03/04/09 constant-context history, supersession, no-change and delta path.
3. TE-P0-06/07 cache diagnostics and break-even policy.
4. TE-P0-14 OpenAI prompt-cache modernization behind model/endpoint capability gates.
5. TE-P0-08 query pushdown.
6. TE-P0-10/11 verified inference skip + macro compiler.
7. TE-P0-12/13 provider-native adapters.
8. P1 residual retrieval/memory/reasoning/output work.
9. P2 local/open-weight experiments only after universal paths are measured.
10. TE-8 provider-receipt end-to-end certification.

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

Inference-skip changes additionally require:

```text
normalized task family
→ complete repository/worktree/dependency/environment fingerprint
→ tool/schema + policy/security fingerprint
→ verifier contract identity
→ exact/allowed semantic reuse gate
→ verifier
→ provider-call avoided receipt
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

The competitive token-economy backlog is an execution/reconciliation overlay, not a tenth roadmap shard and not a CAP-number expansion.

## 11. Source lineage and provenance

The admitted plan combines the original HyperEfficiency design corpus and Token Elimination v10:

- two precursor HyperEfficiency/context-efficiency design inputs;
- V3 numbered roadmap HE-0001..HE-0339;
- V4 numbered roadmap HE-0340..HE-0654;
- V5 numbered roadmap HE-0655..HE-0982;
- V6 numbered roadmap HE-0983..HE-1284;
- Token Elimination v10 HE-1285..HE-1368.

Their SHA-256 fingerprints are retained in the root roadmap registry. The canonical execution authority is the root registry plus the nine bounded shards; raw source prose is not duplicated into the repository merely as archival ballast.

The Context Execution Compiler and Token Economy Competitive Gap documents are architecture/reconciliation overlays derived from current competitive research and the admitted corpus. They do not renumber or silently append capabilities.

## 12. Engineering targets and claim boundary

Targets are workload-scoped engineering gates, not current measured product claims:

- verified exact repeated deterministic task: 0 provider tokens where inference-skip proof is complete;
- easy/warm task: 300-1,500 provider-visible token stretch target;
- easy frontier-needed task: 1K-3K;
- normal coding-agent task: 3K-8K where irreducible evidence fits;
- hard scoped task: 5K-15K before necessity-backed overflow;
- avoidable input/output reduction >=85% floor and >=95% stretch on eligible workloads;
- unexplained envelope overflow = 0;
- task/verifier/security quality non-inferior to baseline.

Component percentages are non-additive. Prompt-cache hits are not context-capacity reduction. Local KV/resource reuse is compute/latency optimization unless provider receipts prove billed-token effects. Post-generation truncation is not output-token savings.

## 13. CI and evidence discipline

- Never dispatch or rerun an equivalent workflow for the same SHA/workflow/input while it is queued or in progress.
- Track the existing `run_id` and continue independent work while CI runs.
- Preserve exact-head certification semantics.
- Do not manufacture provider receipts, independent validation, publication evidence or adoption from repository-local tests.
- Rust transition stays blocked until separate explicit reactivation authority exists.
- External project benchmark numbers remain external evidence until reproduced under frozen Syntavra workloads.

## 14. Completion rule

The admitted roadmap is absorbed only when all **1,368** candidates have a reconciliation classification and every implementation-bearing item has a canonical owner, dependency/invalidation edges, verifier and promotion gate. Candidate count itself is not a progress metric.

The Context Execution Compiler overlay is complete only when all 48 names have a recorded reconciliation result and every promoted transform has target-tokenizer, fidelity, recovery/invalidation and token-per-success evidence appropriate to its class.

The Token Economy Execution Backlog is complete only when each P0/P1/P2 item has a reconciliation result, owner, verifier, invalidation/recovery contract where applicable, and workload-scoped provider-receipt evidence for any claimed savings. P2 experiments do not block universal-core completion unless separately promoted.

> Solve once, verify it, compile the experience into reusable machinery, and make the next equivalent task cheaper or inference-free.
