# Syntavra Python-First Continuation Checklist

Status checkpoint: **2026-08-19**

This document is the operational continuation state for Syntavra. It intentionally separates product development from PR #132 release-authority hardening.

## Hard rules

- [x] Python is the only active feature-development authority for the next phase.
- [x] Rust code, tests, ownership records and contracts are retained.
- [x] Rust production promotion baseline stays **174/245**.
- [x] Remaining Rust public routes stay **71** while Python-first work is active.
- [x] Remaining owned stays **71**, unowned stays **0** unless the canonical inventory itself changes for a separately reviewed reason.
- [x] No 174→245 production promotion before Python COMPLETE.
- [x] No new Rust feature implementation before Python COMPLETE.
- [x] The master roadmap is append-only. Existing roadmap material must not be deleted, shortened or rewritten.
- [x] Existing Python features must be inspected and reused before opening duplicate engines.
- [x] Add a machine-enforced Rust feature-freeze guard once Python-first implementation begins.

Rust exceptions during the freeze are limited to build-blocking repair, security repair, data-loss repair, or minimal contract maintenance required to keep Python development possible. These exceptions must not change the native promotion counter or add product features.

## Repository state at checkpoint

- Admitted `main` before Memory work: `6120ea9d074b0cac0e880e3e9dbf873d1faaec58`.
- Context Reset / Handoff v1 runtime merged through PR #148 and lifecycle certification was admitted through PR #152.
- Active Memory branch: `agent/memory-retrieval-v1`.
- Active PR: #151 — `Add Memory Retrieval v1`.
- Memory Retrieval pre-seal implementation passed its dedicated exact-head workflow and was subsequently scope/lifecycle hardened before admission.
- Python COMPLETE remains false.
- Rust remains feature-frozen at 174/245 production promotion with 71 remaining.

## Immediate exact task

- [x] Merge Context Reset / Handoff v1.
- [x] Implement Memory Retrieval v1 without a parallel memory database.
- [x] Add scoped retrieval, provenance, conflict/supersession, consolidation/forgetting, exact recovery and handoff receipts.
- [x] Harden recovery/mutation/session ownership boundaries and prevent silent memory reactivation.
- [x] Bind Memory Retrieval to exact-head CI, Release Main and immutable action-pin enforcement.
- [ ] Pass final exact-head admission gates on the permanent sealed PR #151 tree.
- [ ] Merge PR #151 only after all load-bearing gates pass.
- [ ] Re-read fresh `main`, then advance to `epistemic_safety_v1`.

## Existing Python baseline: do not reimplement blindly

These are **EXISTS → HARDEN / UNIFY / CERTIFY**, not blank TODOs:

- [x] Fail-closed command rewrite/compaction framework.
- [x] 100+ command-specific output compactors and exact-output recovery.
- [x] Canonical terminal execution, exact disk spooling and streaming summaries.
- [x] Stable/volatile prompt-cache segmentation and stable-prefix planning.
- [x] Deterministic SQLite StructuralIndex.
- [x] Python AST backend, optional tree-sitter backend and deterministic lexical fallback.
- [x] Repository graph/PageRank/blast-radius/hotspot/coupling style analyses.
- [x] SQLite FTS5 repository query path.
- [x] Multi-account provider pool and adaptive model/provider routing foundations.
- [x] OpenAI-compatible, Anthropic and Gemini gateway contracts.
- [x] Hybrid BM25/cosine memory foundation.
- [x] Secret/credential/JWT/private-key redaction and security gates.
- [x] Compact MCP wire/evidence foundation and bounded MCP profiles.
- [x] Project-aware verifier discovery.
- [x] Bounded repository tool loop with search/inspect/impact/diff/verifier actions.
- [x] Exact structured-edit compilation and delivery modes.
- [x] `syntavra agent run` and `agent replay` surfaces.
- [x] Python SDK/product surface, dashboard/PWA and VS Code surfaces.
- [x] Receipt/gate foundations for SignalBench and measured provider usage.

Before creating a new module, inspect the current primitive and prefer composition or contract extension over a duplicate implementation.

## Status labels for the roadmap

Every capability should be classified as one of:

- `EXISTS`: reviewable implementation already exists.
- `HARDEN`: implementation exists but correctness/security/observability needs strengthening.
- `UNIFY`: multiple existing primitives should be composed behind one canonical contract.
- `NEW`: real new implementation is required.
- `CERTIFY`: implementation exists but exact-head/product/benchmark certification is required.
- `EXTERNAL`: proof cannot legitimately be manufactured inside the repository.

## Canonical Python milestone order

Do these in order. Do not begin item N+1 until N has acceptance tests and an exact-head receipt.

- [x] `python_authority_v1`
- [x] `capability_completeness_registry_v1`
- [x] `rust_feature_freeze_guard_v1`
- [x] `universal_context_item_v1`
- [x] `evidence_store_v2`
- [x] `typed_context_object_store_v1`
- [x] `programmatic_execution_v1`
- [x] `deferred_tool_discovery_v1`
- [x] `unified_context_namespace_v1`
- [x] `multi_graph_retrieval_v1`
- [x] `adaptive_context_policy_v1`
- [x] `context_reset_handoff_v1`
- [ ] `memory_retrieval_v1` — current admission candidate
- [ ] `epistemic_safety_v1`
- [ ] `cache_provider_budget_v1`
- [ ] `output_intelligence_v1`
- [ ] `host_adapter_conformance_v1`
- [ ] `observability_attribution_v1`
- [ ] `signalbench_python_product_v1`

## Wave P0-A: authority / contracts / reproducibility

- [ ] Python authority manifest.
- [ ] Python-only development mode.
- [ ] Rust feature-freeze guard.
- [ ] Native promotion counter guard.
- [ ] Capability completeness registry.
- [ ] Capability states: planned / partial / implemented / verified / certified.
- [ ] Public primitive surface budget.
- [ ] Contract/schema version policy.
- [ ] Deterministic fixture catalog.
- [ ] Golden behavior catalog.
- [ ] Exact behavior snapshot.
- [ ] Reproducibility capsule.
- [ ] Exact-head certification primitive.
- [ ] CI Python-first profile.

Exit gate: Python authority explicit, Rust feature freeze machine-enforced, public surface stable, behavior snapshot reproducible.

## Wave P0-B: Evidence Store v2 / universal ContextItem

- [ ] Universal `ContextItem`.
- [ ] Universal `EvidenceItem`.
- [ ] Content-addressed storage and exact hashing.
- [ ] Provenance, trust and taint.
- [ ] Freshness and context leases.
- [ ] Recovery handles and integrity proof.
- [ ] Parent/derived lineage.
- [ ] Secret/PII-aware storage policy.
- [ ] Retention/garbage collection.
- [ ] Evidence mutation journal.

## Wave P0-C: typed context objects

- [ ] `GitDiff`
- [ ] `TestRun`
- [ ] `CompilerDiagnostics`
- [ ] `ASTGraph`
- [ ] `DependencyGraph`
- [ ] `SearchResultSet`
- [ ] `LogStream`
- [ ] `BrowserDOM`
- [ ] `TraceSet`
- [ ] `MetricSeries`
- [ ] `DataFrame`
- [ ] `FileSnapshot`
- [ ] `SymbolSnapshot`
- [ ] `ToolSchemaSet`
- [ ] `MemoryObservation`
- [ ] `TaskStateSnapshot`
- [ ] exact / structural / semantic / bounded-preview representations.
- [ ] deterministic serialization roundtrip tests.

## Wave P0-D: programmatic tool execution

- [ ] Execution-program runtime.
- [ ] call/map/parallel/filter/reduce.
- [ ] sort/top_k/group_by/join/diff.
- [ ] regex/jsonpath/structural-query primitives.
- [ ] Intermediate result suppression.
- [ ] Typed artifact references.
- [ ] Bounded return.
- [ ] Execution receipts.
- [ ] Timeout/cancellation.
- [ ] Side-effect classification.

## Wave P0-E: tool discovery / MCP virtualization

- [ ] Deferred tool loading.
- [ ] Tool namespace tree.
- [ ] Two-stage discovery.
- [ ] Semantic tool families.
- [ ] Capability fingerprints.
- [ ] Host capability negotiation.
- [ ] Tool-schema token budget.
- [ ] Discovery cache.
- [ ] Compatibility/health registry.
- [ ] Risk labels.
- [ ] Tool virtualization.
- [ ] No-tool-needed classifier.
- [ ] Unknown/ambiguous tool fail-closed behavior.

## Wave P0-F: context namespace / progressive disclosure

- [ ] `syntavra://` unified namespace.
- [ ] L0/L1/L2/L3 views.
- [ ] Progressive descent: repo → directory → file → symbol → lines.
- [ ] Context browser.
- [ ] `why` explanation.
- [ ] `reveal` exact recovery.
- [ ] Retrieval trajectory recording.

## Wave P0-G: multi-graph / repository intelligence

- [ ] Semantic graph.
- [ ] Temporal graph.
- [ ] Causal graph.
- [ ] Entity graph.
- [ ] Code graph.
- [ ] Task graph.
- [ ] Provenance graph.
- [ ] Permission/security graph.
- [ ] Unify AST/symbol/import/call/inheritance/dependency indexing.
- [ ] Test-to-source relation.
- [ ] Git history / changed-file / blame weighting.
- [ ] PageRank-style importance.
- [ ] BM25 + vector adapter + reranker + query expansion.
- [ ] Ensemble retrieval.
- [ ] Task-aware map budget.

## Wave P0-H: Adaptive Context Policy v1

Each candidate must be able to carry: importance, task relevance, freshness, trust, taint, security risk, compression risk, recovery cost, provider cost, information gain, redundancy and confidence.

Policy actions:

- [ ] KEEP_EXACT
- [ ] KEEP_STRUCTURAL
- [ ] KEEP_SEMANTIC
- [ ] SUMMARIZE
- [ ] COMPRESS
- [ ] EXTERNALIZE
- [ ] RETRIEVE_ON_DEMAND
- [ ] RESET
- [ ] BRANCH
- [ ] ABSTAIN

Policy dimensions:

- [ ] task-aware
- [ ] model-aware
- [ ] provider-aware
- [ ] repository-aware
- [ ] language-aware
- [ ] security-aware
- [ ] reasoning-level-aware
- [ ] deterministic default policy
- [ ] explainable decision receipt
- [ ] shadow mode for experimental policy

## Wave P0-I: context reset / structured handoff

- [ ] Context age/occupancy/compaction/recovery/drift/retry signals.
- [ ] CONTINUE / COMPACT / RESET / BRANCH decisions.
- [ ] Structured handoff compiler.
- [ ] Handoff verifier.
- [ ] Evidence handles, git state and security state preserved.
- [ ] Reset benchmark arms.

## Wave P0-J: memory / QMD-class retrieval

- [ ] Episodic memory.
- [ ] Semantic memory.
- [ ] Procedural memory.
- [ ] Project/user/temporal memory.
- [ ] Observation timeline.
- [ ] Provenance/supersession/conflict/dedupe.
- [ ] Consolidation/forgetting/importance/recency.
- [ ] BM25/vector/reranking/query expansion.
- [ ] Progressive retrieval and transcript search.
- [ ] Cross-agent memory/evidence handoff.
- [ ] Exact recovery.

## Wave P1: reliability / safety / cost

- [ ] Epistemic state engine.
- [ ] Context Critic.
- [ ] Missing-evidence detection.
- [ ] Information gain / marginal utility.
- [ ] Universal taint propagation and instruction/data separation.
- [ ] Prompt-injection ingress filter.
- [ ] Minimum evidence schemas.
- [ ] Safe action / commit gate and evidence certificates.
- [ ] Agentic abstention.
- [ ] Context leases + dependency invalidation.
- [ ] Prompt Cache Compiler / ROI / cache-bust attribution.
- [ ] Provider-aware budget engine.
- [ ] Output intelligence.
- [ ] Cross-host/SDK conformance.
- [ ] Observability and decision attribution.
- [ ] Fault injection.
- [ ] Semantic preservation verifier.
- [ ] Context quality SLO gate.

## Wave P1-G: SignalBench / external proof

Frozen arms should keep repo, commit, task, model, provider, context window, permissions, timeout, cache policy, hardware class and verifier equal.

- [ ] Plain host baseline.
- [ ] RTK arm.
- [ ] Headroom arm.
- [ ] Token Saver arm.
- [ ] Caveman-style arm.
- [ ] Aider/RepoMap-style arm.
- [ ] OpenClaw native/QMD arm where reproducible.
- [ ] Combined competitor arm.
- [ ] Syntavra minimal.
- [ ] Syntavra balanced.
- [ ] Syntavra adaptive.

Primary metric: **provider-observed total cost / verified successful tasks**.

Secondary metrics: tokens/task, wall-time/task, recovery amplification, critical-evidence recall, context precision/recall, tool-selection accuracy, unsafe-action rate, correct/over-abstention, CPU/RAM/disk overhead and cache hit rate.

## Python COMPLETE gate

- [ ] No required Python capability remains incomplete in the completeness registry.
- [ ] Required unit/integration/security tests PASS.
- [ ] Exact recovery PASS.
- [ ] Deterministic replay PASS.
- [ ] Clean install PASS.
- [ ] Fresh repository smoke PASS.
- [ ] Windows basic runtime PASS.
- [ ] Linux basic runtime PASS.
- [ ] SignalBench Python product suite PASS.
- [ ] Python behavior freeze generated.
- [ ] Python contract freeze generated.
- [ ] Python exact-head certification PASS.
- [ ] Python Completion Certificate generated.

Python COMPLETE proves implementation/certification. It does **not** manufacture external superiority, adoption or maturity claims.

## Rust resume: do not start before Python COMPLETE

- [ ] Export frozen Python→Rust contract corpus.
- [ ] Export golden behavior vectors and receipts.
- [ ] Lift Rust feature-freeze guard.
- [ ] Rebase Remaining-71 differential families on frozen Python product behavior.
- [ ] Port new capabilities Python→Rust.
- [ ] Certify every required differential family.
- [ ] Perform atomic 174→245 production promotion.
- [ ] Run 245/245 post-promotion certification.
- [ ] Keep Python as oracle for a certification window.
- [ ] Decide Rust authority only after all gates pass.

## Required end-of-session checkpoint

Update this block after every implementation session:

```text
CURRENT HEAD:
<sha>

ACTIVE BRANCH:
<branch>

PYTHON STATUS:
<wave / exact checklist item>

RUST STATUS:
FROZEN — 174/245 promoted, 71 remaining

COMPLETED:
- ...

VERIFIED:
- ...

BLOCKERS:
- ...

NEXT EXACT TASK:
- ...
```

## Copy/paste continuation message

```text
Continue Syntavra in PYTHON-FIRST mode.

Read this file and the Python-first roadmap appendix first, then resolve the current GitHub PR/CI state.

Hard rules:
- Master roadmap is append-only. Delete/rewrite nothing from it.
- Python is the only active feature-development engine.
- Rust feature work is frozen.
- Rust promoted-native baseline stays 174/245; 71 remain.
- Do not perform Remaining-71 production promotion.
- Do not change the native promotion counter.
- Do not resume Rust until Python Completion Certificate = PASS.
- Inspect and reuse existing Python primitives before creating duplicate engines.
- Keep public API surface small; compose capabilities behind stable primitives.
- Every implemented capability needs tests, evidence/receipt and acceptance criteria.
- Work on the first unchecked Python task only.
- Do not mark a task complete without verification.

Before broad Python feature coding:
1. Resolve current branch/head.
2. Resolve PR #132 state.
3. If #132 is still open, finish its manifest + exact-head release CI and merge it.
4. Re-read main after merge.

Then continue in this order:
Authority/Contracts
→ Evidence Store v2
→ Typed Context Objects
→ Programmatic Tool Execution
→ Tool Discovery/MCP Virtualization
→ Context Namespace
→ Multi-Graph Retrieval
→ Adaptive Policy
→ Context Reset/Handoff
→ Memory
→ Epistemic/Safety
→ Cache/Provider/Budget
→ Output Intelligence
→ Host Adapters
→ Observability
→ SignalBench
→ Python COMPLETE
→ Rust Resume
→ Differential Port
→ Atomic 245 Promotion
→ Post-Promotion Certification

At the end update:
CURRENT HEAD
COMPLETED
VERIFIED
BLOCKERS
NEXT EXACT TASK
```
