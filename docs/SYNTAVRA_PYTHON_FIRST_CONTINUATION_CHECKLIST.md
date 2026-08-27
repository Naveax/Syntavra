# Syntavra Python-First Continuation Checklist

Status checkpoint: **2026-08-26**

This document is the operational continuation state for Syntavra. It intentionally separates product development from PR #132 release-authority hardening.

## Hard rules

- [x] The Python-first implementation/certification phase is complete; Python remains the frozen reference/oracle for Rust differential work.
- [x] Rust code, tests, ownership records and contracts are retained.
- [x] `PYTHON_COMPLETE = true`; Rust remains retired/frozen and does not auto-resume from Python completion.
- [x] Rust production promotion baseline stays **174/245** with **71** remaining until the separate promotion authority passes.
- [x] Remaining owned stays **71**, unowned stays **0** unless the canonical inventory changes for a separately reviewed reason.
- [x] Rust feature/parity work remains retired for now; only narrow maintenance exceptions are allowed.
- [x] Do not change the native production counter or perform 174→245 promotion before differential and promotion gates pass.
- [x] The master roadmap is append-only. Existing roadmap material must not be deleted, shortened or rewritten.
- [x] Reuse the frozen Python contracts, behavior vectors and receipts as the Rust migration oracle instead of creating parallel authorities.

Maintenance exceptions remain narrow and must never bypass the separate production-promotion authority.

## Repository state at checkpoint

- PR #174 (`Implement Python Completion Certificate v1`) merged to `main` as `e2ead74f70aef8cbf4da333bf19698231b45327b`.
- Merge-push Python Completion Certificate run `32979620715` passed Linux, Windows, aggregate validation and exact-head certificate generation.
- Active phase-exit branch: `agent/python-completion-phase-exit-v1`.
- Python Completion Certificate lifecycle is `certified`; the canonical registry records `PYTHON_COMPLETE = true`, `rust_resume_allowed = false`, and Rust retired/frozen.
- Rust feature/parity work remains retired/frozen at **174/245** with **71** remaining while Python additions, hardening, fixes and certification continue.
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
- [ ] Re-read fresh `main`, then continue Python additions, hardening, fixes and certification. Do not resume Rust/Remaining-71 work unless explicitly reactivated later.

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

## Rust retirement boundary after Python COMPLETE

- [x] `PYTHON_COMPLETE = true` is independent from Rust reactivation.
- [x] `rust_resume_allowed = false` while Rust is retired.
- [x] Rust feature/parity development remains frozen.
- [x] Rust production promotion remains **174/245** with **71** remaining.
- [x] Remaining owned stays **71** and unowned stays **0** unless a separately reviewed canonical inventory change says otherwise.
- [x] Python remains the active product/feature/hardening authority.
- [ ] Do not export or consume the Python→Rust migration corpus as an active port plan while Rust is retired.
- [ ] Do not start Remaining-71 differential/port work while Rust is retired.
- [ ] Reactivate Rust only through a separate explicit, reviewed and admitted reactivation authority.
- [ ] Even after a future reactivation, production promotion remains a separate gate from feature/parity work.

## Required end-of-session checkpoint

Update this block after every implementation session:

```text
CURRENT HEAD:
<sha>

ACTIVE BRANCH:
<branch>

PYTHON STATUS:
<active Python capability / hardening / certification item>

RUST STATUS:
RETIRED/FROZEN — rust_resume_allowed=false; production promotion 174/245; 71 remaining

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
Continue Syntavra in Python-active / Rust-retired mode.

Read docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md, this checklist, the append-only Python-first roadmap, and contracts/python/capability-completeness-registry-v1.json before choosing work.

Hard rules:
- Python is the active product/feature/hardening authority.
- PYTHON_COMPLETE may be true while Rust remains retired.
- rust_resume_allowed=false until a separate explicit Rust-reactivation authority is admitted.
- Rust feature/parity work stays frozen at 174/245 production promotion with 71 remaining.
- Do not start Remaining-71 differential/port work and do not change Rust promotion counters.
- Continue Python roadmap capabilities in dependency order, reusing canonical owners instead of adding parallel stores/engines.
- Capabilities 271–275 are Rust-transition work and remain deferred while Rust is retired.
- Keep external superiority/adoption/maturity claims evidence-gated.
- Before any workflow dispatch/rerun, check queued/in_progress equivalent runs; never use rerun as polling.
```
