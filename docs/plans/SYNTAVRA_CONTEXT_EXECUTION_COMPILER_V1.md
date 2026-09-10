# Syntavra Context Execution Compiler V1

Status: **RECONCILIATION OVERLAY / NO NEW CAPABILITY NAMESPACE**  
Date: **2026-09-07**  
Applies to: **CAP-0281..CAP-1648 admitted HyperEfficiency + Token Elimination roadmap**

## 1. Purpose

Syntavra must not become a pile of independent token compressors. Its competitive core is a **Context Execution Compiler** inside the broader **Verified Experience-Compiling Inference Operating System**:

> Before provider inference, compile the smallest correct, provenance-preserving, verifier-sufficient execution context; avoid repeated reads, repeated commands, repeated tool schemas, repeated reasoning and repeated provider work; externalize recoverable evidence; spend provider tokens only on residual uncertainty.

The governing product KPI remains:

`Verified Provider Cost / Successful Task`

Supporting runtime KPIs for this overlay:

- admitted tokens / candidate tokens;
- provider input tokens / verified success;
- provider output tokens / verified success;
- reasoning tokens / verified success when observable;
- tool-schema tokens / turn;
- duplicate read bytes avoided;
- duplicate command executions avoided;
- exact read-cache hit ratio;
- semantic delta reuse ratio;
- repository reacquisition waste;
- frontier dependency ratio;
- inference-free task fraction;
- verifier pass rate and regression rate;
- recovery-handle success rate;
- cache invalidation correctness;
- p50/p95 local compilation latency.

All percentage claims from external projects are research evidence only. Syntavra may publish a savings claim only after SignalBench/work-equivalence gates and provider-observed receipts.

## 2. Non-duplication rule

The names below are an architecture vocabulary, not permission to create 48 new services.

Before implementation, every row must resolve to an existing canonical owner and be classified with the roadmap states `EXISTS/HARDEN/UNIFY/NEW/CERTIFY/EXTERNAL/DEFERRED`. When an existing Syntavra owner already provides the primitive, extend it instead of creating a second store/router/governor/index.

External projects are never copied into the core merely because they benchmark well. They are treated as one of:

- **PRIMITIVE** — stable low-level building block or protocol;
- **ADAPTER / REFERENCE** — optional integration and competitive reference;
- **OPTIONAL EXPERIMENT** — fidelity-gated optimization that may be disabled;
- **PROVIDER-GATED** — available only when a host/provider exposes it.

## 3. Canonical runtime pipeline

```text
TASK / TURN
  ↓
Context Replay Breaker
  ↓
Context Admission Controller
  ├─ Generated/Vendor Exclusion Engine
  ├─ Monorepo Scope Detector
  ├─ Context Provenance + TTL
  └─ Token Budget Governor
  ↓
Repository Intelligence Fabric
  ├─ ripgrep exact lexical lane
  ├─ Tree-sitter structural lane
  ├─ LSP semantic lane
  ├─ persistent repository graph
  └─ Token Cost-Aware Graph Traversal
  ↓
State + Reuse Plane
  ├─ Persistent State Ledger
  ├─ Content-Addressed Read Cache
  ├─ Semantic Delta Cache
  ├─ Duplicate Read Suppression
  ├─ Duplicate Command Suppression
  └─ Parallel Work Deduplication
  ↓
Tool / Artifact Plane
  ├─ Artifact-First Tool Pipeline
  ├─ Streaming Output Filter
  ├─ Failure Evidence Pinning
  ├─ Test Selection Governor
  ├─ Test Output Compactor
  └─ Broad Search Circuit Breaker
  ↓
Schema / Instruction Plane
  ├─ MCP Schema Governor
  ├─ Schema-on-Demand MCP Proxy
  └─ AGENTS.md Compiler
  ↓
Compression / Serialization Plane (only after selection)
  ├─ Tokenizer-Aware Serializer
  ├─ TOON when measured better for the target model/data shape
  ├─ Headroom/Caveman-compatible recoverable compression adapters
  └─ LLMLingua family only behind fidelity gates
  ↓
Lifecycle Plane
  ├─ Automatic Phase Rotation
  ├─ Checkpoint/Handoff Compiler
  ├─ Subagent Context Firewall
  └─ Native Codex Compaction adapter when host-exposed
  ↓
Inference Economy Plane
  ├─ Reasoning Effort Governor
  ├─ local/deterministic route
  ├─ cheap-model route
  ├─ LiteLLM / OpenRouter / 9Router-compatible provider adapters
  └─ frontier route only for residual uncertainty
  ↓
VERIFY → RECEIPT → LEARN
  ├─ Adaptive Retrieval Learning
  └─ Token-per-Success Optimizer
```

### Ordering invariant

`exclude → scope → deduplicate → retrieve/select → externalize → serialize/compress → route`

Do not semantically compress data that could have been excluded, deduplicated or never admitted.

## 4. Reconciliation matrix for the requested 48 names

| Name | Initial disposition | Canonical direction |
|---|---|---|
| Context Replay Breaker | **UNIFY/HARDEN** | Detect replayed history/tool/file segments by stable identity; replace eligible repeats with state references/deltas. Reuse context decision trace, history/session and cache owners. |
| Token Budget Governor | **HARDEN** | Unify context budget, provider budget and token attribution into per-task/per-lane leases. Fail closed for mandatory evidence. |
| Context Admission Controller | **HARDEN/UNIFY** | Make `context_governor.pack_context` the policy kernel for pre-prompt admission; add proof/relevance/TTL/cache economics without creating a second governor. |
| Persistent State Ledger | **UNIFY/HARDEN** | Reuse canonical evidence/state/usage ledgers. Append-only state transitions, crash-safe recovery, provenance and invalidation edges. |
| Semantic Delta Cache | **NEW or HARDEN after reconciliation** | Store semantic state deltas keyed by repository/task lineage; never substitute stale semantic state without dependency invalidation. |
| Content-Addressed Read Cache | **NEW or HARDEN after reconciliation** | Exact read cache keyed by content digest + representation + range/options; safe reuse is independent of path rename when content identity is unchanged. |
| Graphify | **ADAPTER / REFERENCE** | Do not create a Syntavra core service named after the external project. Borrow persistent explainable graph ideas and optionally ingest/export its graph format. |
| Serena | **ADAPTER / REFERENCE** | Use as a semantic code-navigation benchmark/adapter. Syntavra owns its retrieval policy; Serena may provide symbol/refactor operations where installed. |
| Aider Repo-Map | **REFERENCE ALGORITHM** | Reproduce the useful pattern, not the product dependency: symbol graph + graph ranking + token-budgeted repo map. |
| Tree-sitter | **PRIMITIVE** | Deterministic incremental syntax/structure extraction. Primary AST/symbol source where grammar coverage is adequate. |
| LSP | **PRIMITIVE / PROTOCOL** | Semantic definitions/references/types/diagnostics. Use to confirm or enrich structural graph edges, not as the only index. |
| ripgrep | **PRIMITIVE** | Fast exact lexical retrieval/fallback. Respect ignore rules and generated/vendor exclusions by default. |
| RTK | **ADAPTER / REFERENCE** | Optional shell-output compressor. Do not stack another lossy shell compressor on the same output. Syntavra still owns artifact storage, evidence and budgets. |
| Artifact-First Tool Pipeline | **CORE HARDEN** | Full/raw tool payload goes to an addressable artifact first; model receives bounded structured view + handle + provenance. |
| Streaming Output Filter | **CORE NEW/HARDEN** | Filter progress/noise/repetition while stdout/stderr streams, with error/failure lines pinned and raw stream recoverable. |
| Failure Evidence Pinning | **CORE HARDEN** | Failure signatures, error lines, stack roots, failed test IDs and verifier evidence cannot be evicted by generic compression. |
| Test Output Compactor | **CORE HARDEN** | Framework-aware pass/fail summary, failures and minimal diagnostics; raw output remains an artifact. |
| Test Selection Governor | **CORE NEW** | Select tests from changed symbols, dependency graph, prior failures and risk; expand on uncertainty/failure. Never claim full verification after a partial set. |
| Duplicate Read Suppression | **CORE NEW/HARDEN** | Suppress identical reads using content/range/options identity and task-local read ledger; return a stable handle/delta when possible. |
| Duplicate Command Suppression | **CORE NEW/HARDEN** | Skip only provably equivalent safe/read-only work. Key includes command, cwd, git tree/SHA or relevant dependency digest, env/toolchain and input artifact identities. Writes are not blindly replayed. |
| Broad Search Circuit Breaker | **CORE NEW** | Detect repeated widening searches, cap breadth/bytes/calls, require evidence-based scope escalation and terminate search loops. |
| Automatic Phase Rotation | **HARDEN/UNIFY** | Extend context-pressure actions into semantic phases: discover → inspect → edit → verify → handoff. Rotation writes a checkpoint before dropping phase-local context. |
| Checkpoint/Handoff Compiler | **HARDEN** | Extend existing context-reset handoff: compile goals, decisions, changed files, evidence, failures, open risks and next actions into typed state instead of prose-only summary. |
| Native Codex Compaction | **PROVIDER/HOST-GATED ADAPTER** | Detect and cooperate with native Codex autocompaction/manual compaction when exposed. Never depend on undocumented internals; persist Syntavra state before host compaction. |
| MCP Schema Governor | **CORE NEW/HARDEN** | Budget, rank, pin and expire tool schemas. Prefer tool-search metadata over eager full schema exposure. Track schema-token receipts. |
| Schema-on-Demand MCP Proxy | **CORE NEW/UNIFY** | Expose compact discovery/meta-tools and materialize full schema only for selected tools. Cache schema by digest; invalidate on upstream change. |
| AGENTS.md Compiler | **CORE NEW** | Compile scoped instructions by path/precedence/provenance; remove duplicates, split static/cacheable from dynamic content and emit host-specific forms without changing semantics. |
| Ponytail | **REFERENCE / OPTIONAL POLICY** | Reuse YAGNI/reuse-first lessons as a task policy, not a permanent prompt tax. Enable only when A/B evidence improves token-per-success for the target model/workload. |
| Caveman | **ADAPTER / REFERENCE** | Benchmark terse output and recoverable context compression. Never use terse mode for security/irreversible ambiguity; keep exact artifacts. |
| Headroom | **ADAPTER / REFERENCE** | Optional recoverable compressor/backend and benchmark competitor. Do not double-compress the same segment; require tokenizer-measured no-expansion and recovery. |
| LLMLingua | **OPTIONAL EXPERIMENT** | Semantic prompt compression only after deterministic selection; disabled for exact instructions/code/evidence by default. |
| LLMLingua-2 | **OPTIONAL EXPERIMENT** | Preferred LLMLingua-family candidate for task-agnostic extractive compression; quality/fidelity and latency gates required per model/workload. |
| LongLLMLingua | **OPTIONAL EXPERIMENT** | Long-document/RAG lane only; query-aware compression, never a universal coding-context pass. |
| TOON | **OPTIONAL FORMAT** | Candidate representation for suitable structured arrays. Use only when target tokenizer measurement beats canonical JSON/TSV and round-trip validation passes. |
| Tokenizer-Aware Serializer | **CORE NEW/HARDEN** | Choose representation from actual target tokenizer/model and data shape; field projection occurs before serialization. No universal “TOON always wins” rule. |
| Generated/Vendor Exclusion Engine | **CORE NEW/HARDEN** | Central exclusion policy for node_modules, target, dist, build, vendor, generated/minified/binary/cache artifacts, with explicit override and provenance. |
| Monorepo Scope Detector | **CORE NEW/HARDEN** | Detect package/workspace boundaries, ownership, changed package closure and relevant instruction scopes before retrieval. |
| Large Document Local RAG | **CORE HARDEN/NEW** | External index for large docs/logs/data. Hybrid lexical/semantic retrieval, rerank/dedup, bounded excerpts and exact source handles. |
| Subagent Context Firewall | **CORE NEW** | Child agent gets task-minimal scoped context, budgets and capabilities; parent history is not inherited wholesale. Safety and mandatory repository constraints remain inherited. |
| Parallel Work Deduplication | **CORE NEW** | Work-key registry prevents equivalent subagents/commands/retrieval jobs from running concurrently; share result artifacts and in-flight futures. |
| Reasoning Effort Governor | **CORE POLICY + PROVIDER-GATED EXECUTION** | Task difficulty/uncertainty/verifier risk determine effort. Escalation requires evidence; supported provider knobs are adapters, not universal assumptions. |
| 9Router | **ADAPTER / COMPETITIVE REFERENCE** | Provider/fallback gateway compatibility only. Do not make its quota/account logic a Syntavra core dependency. |
| LiteLLM | **ADAPTER** | Optional unified provider/proxy backend for routing, budgets and fallback. Syntavra remains the policy/evidence owner. |
| OpenRouter | **ADAPTER / EXTERNAL PROVIDER GATEWAY** | Optional multi-provider route. Record the actual chosen model/provider/cost receipt; external routing cannot bypass Syntavra verification policy. |
| Token Cost-Aware Graph Traversal | **CORE NEW** | Search graph paths with expected information gain / token cost / verification value, not only PageRank or shortest path. |
| Context Provenance + TTL | **CORE HARDEN** | Every context item carries source digest, created/observed time, repository lineage, validity/invalidation edges, recovery policy and freshness class. |
| Adaptive Retrieval Learning | **LATE CORE NEW** | Learn retrieval/ranking only after deterministic baseline and logging exist. Offline evaluation + shadow mode + rollback required before promotion. |
| Token-per-Success Optimizer | **CORE META-OPTIMIZER** | Optimize verified provider cost, latency and success jointly. It may change budgets/routing/retrieval policies only behind work-equivalence and regression gates. |

## 5. External-project boundary

### Adopt as primitives

- **Tree-sitter** for incremental structural parsing.
- **LSP** as semantic protocol/adapter surface.
- **ripgrep** for exact lexical search.

These primitives are composable and do not own Syntavra's policy.

### Integrate or benchmark, do not vendor into the core by default

- Graphify;
- Serena;
- Aider Repo-Map;
- RTK;
- Ponytail;
- Caveman;
- Headroom;
- 9Router;
- LiteLLM;
- OpenRouter.

Each adapter must declare version, license, capability, trust boundary, side effects, token accounting method and fallback behavior.

### Experimental compression/format lane

- LLMLingua;
- LLMLingua-2;
- LongLLMLingua;
- TOON.

Default order is **selection before compression**. These may never rewrite security policy, exact user constraints, error evidence, source code required for editing, verifier contracts or signatures without an exact recovery path and fidelity verifier.

## 6. Missing pieces added to the plan

The requested list is strong but needs these cross-cutting capabilities to avoid impressive-looking incorrect savings:

1. **Invalidation Graph** — content, symbol, environment, toolchain and schema changes invalidate dependent cache entries.
2. **No-Expansion Gate** — a transform that is not smaller under the target tokenizer returns the original representation.
3. **Exact Recovery Handles** — every lossy/reduced artifact exposes a content-addressed path back to original evidence when policy permits.
4. **Field Projection Engine** — remove irrelevant API/JSON fields before TOON/serialization/compression.
5. **Prompt-Prefix Stability Planner** — stable/cacheable prefix first; volatile timestamps/IDs/state in a suffix to preserve provider prompt-cache utility.
6. **Tool Discovery Index** — BM25 + semantic + usage/permission/risk signals for Schema-on-Demand MCP selection.
7. **Work Equivalence Contract** — benchmark arms must perform equivalent work under the same verifier; partial work may not masquerade as token savings.
8. **Shadow/Counterfactual Policy Evaluation** — candidate policies are replayed offline before live promotion.
9. **Policy Rollback** — automatic demotion when success, security, latency or token-per-success regresses.
10. **Provider Receipt Reconciliation** — local estimates and provider-observed usage are separate evidence classes.
11. **Cache Poisoning / Provenance Guard** — untrusted or stale retrieved state cannot become authoritative merely because it was cached.
12. **Capability/Privacy Boundary** — subagents, local RAG and external gateways receive the minimum data and tool permissions required.

Most of these already have conceptual owners in the admitted HyperEfficiency/Token Elimination roadmap; reconciliation must reuse them rather than allocate new CAP numbers automatically.

## 7. Implementation waves

### CX-0 — Reconciliation and measurement

- map all 48 names to current Python owners and admitted CAP rows;
- mark `EXISTS/HARDEN/UNIFY/NEW/CERTIFY/EXTERNAL/DEFERRED`;
- freeze baseline SignalBench workloads and work-equivalence contracts;
- add per-segment token attribution: instructions, history, repo, schemas, outputs, retrieved docs, generated answer and reasoning when provider-reported.

**Exit:** no duplicate owner introduced; baseline receipts reproducible.

### CX-1 — Zero-risk waste elimination

- Generated/Vendor Exclusion Engine;
- Monorepo Scope Detector;
- Duplicate Read Suppression;
- Content-Addressed Read Cache;
- Duplicate Command Suppression for provably safe reads;
- Broad Search Circuit Breaker;
- Context Provenance + TTL.

**Exit:** exactness-preserving reductions only; stale-cache mutation tests pass.

### CX-2 — Repository Intelligence Fabric

- Tree-sitter + LSP + ripgrep fusion;
- persistent symbol/dependency/reference graph;
- Aider-style token-budgeted map;
- Token Cost-Aware Graph Traversal;
- Graphify/Serena interoperability experiments.

**Exit:** repository task set shows lower reacquisition tokens/calls without lower verifier success.

### CX-3 — Artifact-first tools and tests

- raw output → immutable/recoverable artifact;
- Streaming Output Filter;
- Failure Evidence Pinning;
- Test Output Compactor;
- Test Selection Governor;
- RTK adapter arbitration.

**Exit:** failure recall = 100% on golden failure fixtures; raw artifact recovery = 100%; no double compression.

### CX-4 — MCP and instruction compilation

- Tool Discovery Index;
- MCP Schema Governor;
- Schema-on-Demand MCP Proxy;
- AGENTS.md Compiler with path scope/precedence/provenance;
- schema cache invalidation.

**Exit:** same tool-selection/verifier success under a frozen MCP benchmark with materially lower schema tokens; instructions precedence golden tests pass.

### CX-5 — Replay, state and lifecycle

- Context Replay Breaker;
- Persistent State Ledger;
- Semantic Delta Cache;
- Automatic Phase Rotation;
- Checkpoint/Handoff Compiler;
- Subagent Context Firewall;
- Parallel Work Deduplication;
- Native Codex Compaction cooperation where exposed.

**Exit:** long-session tasks survive phase/session rotation with exact open-work/evidence reconstruction; repeated-work metrics fall.

### CX-6 — Serialization and recoverable compression

- Field Projection Engine;
- Tokenizer-Aware Serializer;
- TOON candidate lane;
- Headroom/Caveman adapters;
- LLMLingua family experimental lanes;
- No-Expansion Gate + Exact Recovery Handles.

**Exit:** every promoted transform wins under the target tokenizer and passes semantic/exactness class-specific fidelity tests. Compression is automatically bypassed when it does not win.

### CX-7 — Inference economy

- Token Budget Governor leases;
- Reasoning Effort Governor;
- deterministic/local/cheap/frontier escalation;
- LiteLLM/OpenRouter/9Router adapters;
- provider receipt reconciliation.

**Exit:** lower verified provider cost per successful task with equivalent verifier conditions and no security regression.

### CX-8 — Adaptive optimization

- Adaptive Retrieval Learning;
- Token-per-Success Optimizer;
- shadow/counterfactual policy evaluation;
- guarded online promotion and rollback.

**Exit:** learned policy must beat deterministic baseline on held-out repositories/tasks before promotion; regression triggers automatic rollback.

## 8. Hard invariants

1. **Correctness before token count.** A cheaper wrong task is a failure, not a saving.
2. **Selection before compression.** Never pay to compress what should not be admitted.
3. **Exact evidence survives.** Failures, verifier evidence and security constraints are pinned or recoverable.
4. **No duplicate active work.** Equivalent reads, commands, subagents and CI runs coalesce when safe.
5. **Cache requires provenance and invalidation.** A hit without validity proof is a miss.
6. **Provider-specific features are optional.** Native compaction/reasoning/cache knobs cannot become universal core requirements.
7. **No double compression.** One segment has one active transform owner at a time.
8. **No-expansion.** If the target tokenizer says the transformed form is not smaller, send the original.
9. **All learning is reversible.** Learned policies start offline/shadow and have rollback.
10. **Provider receipts outrank local token estimates for billing claims.**

## 9. Competitive evidence to reproduce, not inherit

As of 2026-09-07, the research set includes:

- Aider Repo-Map: Tree-sitter symbol/ref extraction plus graph ranking under a configurable map token budget.
- Graphify: persistent explainable code/document knowledge graph with deterministic Tree-sitter structural extraction.
- Serena: semantic code retrieval/editing via language-server-backed tooling.
- RTK: command-specific shell-output filtering/compression.
- slim-mcp / mcp-context-proxy and SCOUT: lazy/schema-on-demand MCP tool discovery patterns.
- Caveman: terse output plus local recoverable context compression patterns.
- Headroom: local/recoverable context compression and retrieval patterns.
- LLMLingua family: semantic prompt compression research.
- Ponytail: reuse/YAGNI behavior-policy experiments; results are model/workload dependent.
- LiteLLM, OpenRouter and 9Router: gateway/routing/fallback patterns.
- Codex: scoped AGENTS.md instruction hierarchy and native context compaction surfaces where available.

No external benchmark number is a Syntavra result. Reproduce useful claims against frozen Syntavra workloads before adoption or marketing.

## 10. End state

Syntavra wins when a coding agent no longer has to repeatedly rediscover, reread, reserialize, reschema, rethink and resend the same evidence.

The target behavior is:

```text
know what is already known
→ prove what changed
→ retrieve the minimum sufficient evidence
→ execute the minimum necessary work
→ preserve exact failures and provenance
→ invoke the cheapest sufficient inference path
→ verify success
→ learn only from verified outcomes
```

That is the architectural boundary between a token compressor and a market-leading context execution system.
