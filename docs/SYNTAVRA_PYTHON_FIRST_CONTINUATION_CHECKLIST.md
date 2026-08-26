# Syntavra Python-First Continuation Checklist

Status checkpoint: **2026-08-26**

This document is the operational continuation state for Syntavra. It intentionally separates product development from PR #132 release-authority hardening.

## Hard rules

- [x] The Python-first implementation/certification phase is complete; Python remains the frozen reference/oracle for Rust differential work.
- [x] Rust code, tests, ownership records and contracts are retained.
- [x] `PYTHON_COMPLETE = true` and Rust feature/parity development resume is allowed only through the canonical completion state.
- [x] Rust production promotion baseline stays **174/245** with **71** remaining until the separate promotion authority passes.
- [x] Remaining owned stays **71**, unowned stays **0** unless the canonical inventory changes for a separately reviewed reason.
- [x] Rust feature/parity work may resume after Python COMPLETE, but this does not grant production promotion credit.
- [x] Do not change the native production counter or perform 174→245 promotion before differential and promotion gates pass.
- [x] The master roadmap is append-only. Existing roadmap material must not be deleted, shortened or rewritten.
- [x] Reuse the frozen Python contracts, behavior vectors and receipts as the Rust migration oracle instead of creating parallel authorities.

Maintenance exceptions remain narrow and must never bypass the separate production-promotion authority.

## Repository state at checkpoint

- PR #174 (`Implement Python Completion Certificate v1`) merged to `main` as `e2ead74f70aef8cbf4da333bf19698231b45327b`.
- Merge-push Python Completion Certificate run `32979620715` passed Linux, Windows, aggregate validation and exact-head certificate generation.
- Active phase-exit branch: `agent/python-completion-phase-exit-v1`.
- Python Completion Certificate lifecycle is `certified`; the canonical registry records `PYTHON_COMPLETE = true` and `rust_resume_allowed = true`.
- Rust feature/parity work may resume, while production promotion remains frozen at **174/245** with **71** remaining.
- External superiority, adoption, marketplace maturity and long-lived real-world claims remain external-evidence claims and are not manufactured by repository self-certification.

## Immediate exact task

- [x] Merge PR #174 after exact-head completion, Release Main, Rust Freeze, Capability Completeness and SignalBench gates passed.
- [x] Verify merge-push Completion Certificate run `32979620715` on `main`.
- [x] Materialize the final Python phase-exit registry, authority and Rust-resume transition without changing production promotion.
- [ ] Reconcile all historical milestone certifiers with the canonical post-completion global lifecycle state.
- [ ] Remove all temporary phase-exit helper files and verify a helper-free manifest/diff.
- [ ] Open the final phase-exit PR from `agent/python-completion-phase-exit-v1`.
- [ ] Require exact-head Linux + Windows Completion Certificate `PASS` plus all load-bearing CI on the final PR head.
- [ ] Merge the final phase-exit seal only after every exact-head gate is green.
- [ ] Re-read fresh `main`, export the frozen Python→Rust contract/behavior corpus, then continue Remaining-71 differential work. Production promotion remains a later separate gate.

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
- [x] `memory_retrieval_v1`
- [x] `epistemic_safety_v1`
- [x] `cache_provider_budget_v1`
- [x] `output_intelligence_v1`
- [x] `host_adapter_conformance_v1`
- [x] `observability_attribution_v1`
- [x] `signalbench_python_product_v1`
- [x] `python_completion_certificate_v1`

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

- [x] No required Python capability remains incomplete in the completeness registry.
- [x] Required unit/integration/security tests PASS.
- [x] Exact recovery PASS.
- [x] Deterministic replay PASS.
- [x] Clean install PASS.
- [x] Fresh repository smoke PASS.
- [x] Windows basic runtime PASS.
- [x] Linux basic runtime PASS.
- [x] SignalBench Python product suite PASS.
- [x] Python behavior freeze generated.
- [x] Python contract freeze generated.
- [x] Python exact-head certification PASS.
- [x] Python Completion Certificate generated.

Python COMPLETE proves implementation/certification. It does **not** manufacture external superiority, adoption or maturity claims.

## Rust resume: do not start before Python COMPLETE

- [ ] Export frozen Python→Rust contract corpus.
- [ ] Export golden behavior vectors and receipts.
- [x] Lift Rust feature-freeze guard for feature/parity development; keep production promotion frozen.
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
FEATURE/PARITY RESUME ALLOWED — production promotion still 174/245, 71 remaining

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
Continue Syntavra from the post-Python-COMPLETE Rust-resume boundary.

Read this file and the Python-first roadmap appendix first, then resolve the current GitHub PR/CI state.

Hard rules:
- Master roadmap is append-only. Delete/rewrite nothing from it.
- Python Completion Certificate is PASS/certified and Python stays the frozen reference/oracle.
- Rust feature/parity development may resume.
- Rust production promotion remains 174/245 with 71 remaining.
- Do not perform the atomic 174→245 production promotion until the separate differential and promotion authorities pass.
- Do not change the native production counter merely because Rust development resumed.
- Reuse frozen Python contracts, golden behavior vectors and receipts as migration authority.
- Keep public API surface small and avoid duplicate engines/authorities.
- Every Rust differential/port step needs tests, exact-head evidence and acceptance criteria.
- Work on the first unchecked Rust-resume/differential task only.
- Do not mark a task complete without verification.

Before new Rust feature/parity work:
1. Resolve fresh `main`, current branch/head and the final phase-exit PR/CI state.
2. If the phase-exit PR is still open, finish exact-head Linux/Windows Completion Certificate and all load-bearing CI, then merge it.
3. Re-read `main` after merge.
4. Export the frozen Python→Rust contract corpus and golden behavior vectors/receipts.
5. Rebase Remaining-71 differential families on the frozen Python product behavior.

Then continue in this order:
Frozen Python contract corpus
→ Golden behavior vectors/receipts
→ Remaining-71 differential rebase
→ Rust capability ports
→ Required differential certification
→ Atomic 174→245 promotion
→ 245/245 post-promotion certification
→ Python-oracle certification window
→ Rust authority decision

At the end update:
CURRENT HEAD
COMPLETED
VERIFIED
BLOCKERS
NEXT EXACT TASK
```
