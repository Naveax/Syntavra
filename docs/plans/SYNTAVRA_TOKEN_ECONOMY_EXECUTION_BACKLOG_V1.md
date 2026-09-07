# Syntavra Token Economy Execution Backlog V1

Status: ACTIVE CONTINUATION BACKLOG / RECONCILIATION-FIRST / NO NEW CAP NAMESPACE  
Date: 2026-09-07  
Authority: `HYPEREFFICIENCY_MASTER_ROADMAP_V6` + Context Execution Compiler + Provider Token Envelope + SWIR + Token Economy Competitive Gap V1

## 1. Purpose

This file is the operational TODO/continuation list for token-economy work discovered through competitive research. It does **not** create a parallel capability namespace and does not imply every named mechanism is a missing implementation.

Before implementation, every item must be reconciled against existing canonical owners and classified:

`EXISTS | HARDEN | UNIFY | NEW | CERTIFY | EXTERNAL | DEFERRED`

The optimization order is fixed:

`do not create -> do not retrieve -> do not resend -> do not reason again -> do not generate -> compact only the irreducible residual`

The global execution ordering invariant remains:

`exclude -> scope -> deduplicate -> retrieve/select -> externalize -> project -> serialize/compile -> optional fidelity-gated compress -> route -> verify -> learn`

## 2. Current completion snapshot

### Implemented/hardened in this branch

- [x] **TE-P0-05 Stable Tool Schema Canonicalization**
  - canonical tool declaration ordering;
  - stable compiled/catalog hashes for equivalent effective tool sets;
  - canonicalization of semantically unordered schema arrays such as `required` where protocol semantics permit;
  - reverse-registration regression coverage;
  - implementation commit before this backlog: `8d78cfc0b66d01eea5f01b1aec0ad255ef5b692d`.

This status is implementation evidence, not proof of provider-billed savings. Provider-observed cache receipts are still required for savings claims.

## 3. P0: provider-work elimination before compression

These are the first implementation priorities after reconciliation and measurement.

### TE-P0-01 Pre-Model Ingestion Fold Gate

- [ ] Reconcile `ToolOutputExternalizer` + `agent_runtime` owners.
- [ ] Persist raw result before provider admission.
- [ ] Fold oversized success output on first visibility, not after it has already polluted history.
- [ ] Emit bounded preview + critical evidence + exact recovery handle.
- [ ] Give failures larger visible evidence leases than successes.
- [ ] Bound recalls by range/token budget so recovery cannot re-flood context.
- [ ] Learn repeated-recall families and adjust warm leases without weakening exact recovery.
- [ ] Add provider-visible-before/after attribution.

Exit: no oversized tool payload silently becomes recurring provider history.

### TE-P0-02 Active Context Supersession Graph

- [ ] Define identity keys for file reads, diffs, diagnostics, tests, API/tool snapshots and generated evidence.
- [ ] Record `superseded_by` edges when a newer exact view replaces an older view.
- [ ] Convert superseded bodies to handle-only receipts while keeping exact artifacts recoverable.
- [ ] Make invalidation explicit for worktree/environment/toolchain changes.
- [ ] Pin security, failure and verifier-critical evidence so it cannot be superseded incorrectly.

Exit: stale exact bodies stop consuming active provider context while lineage remains recoverable.

### TE-P0-03 Useless / No-Change Result Elision

- [ ] Detect empty, identical, no-change, already-known and unchanged polling results.
- [ ] Replace repeated prose/raw payloads with typed compact receipts.
- [ ] Preserve command/tool identity, baseline hash and time/provenance where needed.
- [ ] Fail open to full evidence when equivalence cannot be proven.

Exit: repeated no-op evidence costs approximately constant metadata, not repeated payload size.

### TE-P0-04 Causal History Skeleton / Constant-Context Tool Loop

- [ ] Separate active evidence from historical causal receipts.
- [ ] Keep user intent, action, outcome, hash, failure, verifier state and exact handles.
- [ ] Drop stale payload bodies after lease/supersession/folding rules permit.
- [ ] Represent current diffs as changed hunks/symbols + artifact handle rather than whole repeated tails.
- [ ] Collapse successful verifier logs to status/count/receipt while pinning minimal failure evidence.
- [ ] Add long-session test proving active context is approximately O(active evidence), not O(tool rounds).

Exit: provider-visible tool history stays roughly bounded after warm-up.

### TE-P0-05 Stable Tool Schema Canonicalization

- [x] Canonical tool ordering implemented.
- [x] Stable equivalent-set hashes implemented.
- [x] Semantically unordered schema-array canonicalization implemented where permitted.
- [ ] Add provider receipt benchmark for warm cache hit-rate improvement.
- [ ] Extend stability fingerprints to all provider-visible schema/profile surfaces.

Exit: same model + same stable state + same effective tool set produces byte-stable tool prefix.

### TE-P0-06 Prompt Cache Break Detector

- [ ] Hash prefix components independently: system/instructions/tools/schema/policy/model/cache controls/static resources.
- [ ] Compare provider cached-token diagnostics with local fingerprints.
- [ ] Attribute break reason instead of reporting a generic miss.
- [ ] Distinguish expected TTL expiry from accidental prefix mutation.
- [ ] Add telemetry for volatile fields that entered stable prefix.

Exit: cache misses are explainable and actionable.

### TE-P0-07 Cache Break-Even Eviction Governor

- [ ] Estimate warm-prefix rewrite penalty.
- [ ] Estimate expected future token waste prevented by eviction/compaction.
- [ ] Preserve warm prefix when mutation costs more than expected avoided waste.
- [ ] Bypass economics for correctness/security/staleness hazards.
- [ ] Shadow-test decisions before adaptive promotion.

Exit: context pruning and prompt caching optimize one economic objective instead of fighting each other.

### TE-P0-08 Tool Result Query Pushdown

- [ ] Add safe local/server-side field projection.
- [ ] Add row/range limiting before provider visibility.
- [ ] Add predicate filtering.
- [ ] Add count/sum/min/max/group/top-k/sort/sample operators where result types support them.
- [ ] Store exact raw artifact before any lossy aggregation where recovery may be required.
- [ ] Add capability/privacy policy so forbidden data cannot be exposed merely because aggregation is available.

Target pipeline:

`tool -> project -> where -> aggregate -> sort/top-k -> sample -> exact artifact + compact result`

Exit: provider-visible result size is driven by answer shape, not raw dataset size.

### TE-P0-09 Delta Tool Response Protocol

- [ ] Define stable baseline handles for polling/repeated tool results.
- [ ] Emit only changed fields/segments when exact delta is valid.
- [ ] Include baseline identity and invalidation fingerprint.
- [ ] Fall back to full current result when baseline is unavailable/stale/ambiguous.
- [ ] Benchmark polling/tool-loop cumulative savings.

Exit: near-identical repeated results do not resend the unchanged body.

### TE-P0-10 Verifier-Gated Inference Skip Cache

- [ ] Define normalized task-family identity.
- [ ] Fingerprint repository/worktree/dependencies/toolchain/environment.
- [ ] Fingerprint policy/security/tool schema/verifier contract.
- [ ] Permit exact verified cache/macro hits to bypass provider inference entirely.
- [ ] Require verifier/entailment gates for any semantic reuse path.
- [ ] Record prevented false semantic hits.
- [ ] Fail closed on incomplete fingerprints.

Exit: eligible repeated deterministic tasks can consume **0 provider tokens**.

### TE-P0-11 Workflow Skill / Macro Compiler

- [ ] Reconcile admitted Macro Factory, deterministic workflow compiler and experience compiler owners.
- [ ] Compile repeated verified tool graphs into parameterized macros/skills.
- [ ] Version macro identity with tools, environment, policy and verifier contract.
- [ ] Re-verify macro execution before returning success.
- [ ] Invalidate on incompatible repository/toolchain/policy changes.
- [ ] Measure provider calls avoided per verified macro execution.

Exit: the model chooses/reuses a verified workflow rather than re-planning every step.

### TE-P0-12 Provider-Native Context Editing Adapter

- [ ] Capability-detect provider-native tool/thinking/history clearing.
- [ ] Prefer native context editing where end-to-end benchmark wins.
- [ ] Reconcile provider cleared-token diagnostics into usage receipts.
- [ ] Keep portable Syntavra fallback without requiring provider-native behavior.

Exit: repeated provider processing can be removed at the provider boundary where supported.

### TE-P0-13 Provider-Native Tool Search Adapter

- [ ] Capability-detect native deferred/dynamic tool search.
- [ ] Prefer provider-native search when it beats Syntavra gateway end-to-end.
- [ ] Avoid double retrieval layers by default.
- [ ] Preserve auth/risk/capability filters before tool execution.

Exit: large tool catalogs are not eagerly serialized when the provider already offers a better deferred mechanism.

### TE-P0-14 OpenAI Prompt Cache Modernization

- [ ] Version/capability-gate current OpenAI prompt-cache request controls.
- [ ] Remove automatic dependence on deprecated retention fields for models/endpoints that no longer use them.
- [ ] Integrate current cache diagnostics into `PromptCacheOptimizer` / receipt reconciliation.
- [ ] Preserve older endpoint compatibility behind explicit adapter capability detection.
- [ ] Test that modern and legacy request builders do not contaminate one another's stable prefix.

Exit: current OpenAI models use current cache controls without breaking legacy compatibility.

## 4. P1: residual retrieval, memory, reasoning and output reduction

### TE-P1-01 Hybrid Tool Search: BM25 + dense + RRF

- [ ] Add sparse lexical/BM25 retrieval.
- [ ] Add dense retrieval adapter.
- [ ] Fuse with reciprocal-rank fusion.
- [ ] Apply authorization, risk, health and capability filters before schema materialization.
- [ ] Keep deterministic fallback and fixed top-k/token budget.

### TE-P1-02 Multi-Level Tool Detail

- [ ] L0: name/id only.
- [ ] L1: name + compact description/capability tags.
- [ ] L2: full selected schema.
- [ ] Tokenizer-measure every level and apply no-expansion.

### TE-P1-03 Task-Aware Residual Code Pruner

- [ ] Run only after monorepo scope + graph/symbol/range selection + deterministic pruning.
- [ ] Optional lightweight local skimmer for remaining large code blocks.
- [ ] Never prune exact edit anchors, security constraints or verifier-critical evidence.
- [ ] Fidelity gate and deterministic fallback required.

### TE-P1-04 Deterministic Mask/Supersede Before Summary

- [ ] Enforce compaction ordering: drop -> dedup -> supersede -> mask -> handle -> delta -> deterministic summary -> LLM summary.
- [ ] Track summary-generation tokens as optimization overhead.
- [ ] Refuse LLM summarization when cheaper exact reduction already meets the envelope.

### TE-P1-05 Phase / Trajectory-Aware Compaction

- [ ] Add semantic triggers: exploration resolved, target localized, edit accepted, verifier entered, failure resolved, phase complete.
- [ ] Keep threshold-based pressure as safety fallback, not sole trigger.
- [ ] Shadow-test learned/adaptive timing policies.

### TE-P1-06 Dual-Track Raw + Constructed Memory

- [ ] Exact/raw events remain immutable authority.
- [ ] Constructed memory is accelerator only.
- [ ] Retrieval can query both and jointly rerank.
- [ ] Constructed memory may never erase raw lineage.

### TE-P1-07 Multi-Signal Memory Reranker

- [ ] Semantic similarity.
- [ ] Lexical/BM25 relevance.
- [ ] Entity/symbol/repository relevance.
- [ ] Temporal/current-state relevance.
- [ ] Provenance/freshness/TTL.
- [ ] Fixed provider-token admission budget.

### TE-P1-08 Reasoning Sketch Governor

- [ ] Budget input, output and reasoning separately.
- [ ] Use native provider reasoning-effort/verbosity controls first.
- [ ] Route compatible low-risk tasks to compact reasoning contracts.
- [ ] Keep local/open-weight learned pruning experimental until verifier-gated.
- [ ] Attribute reasoning tokens from provider receipts where available.

### TE-P1-09 Dynamic Output Contract + Semantic Novelty Gate

- [ ] Choose minimal answer schema before generation.
- [ ] Generate compact semantic facts/actions/evidence/uncertainty only.
- [ ] Reject filler and repeated known information unless explicitly requested.
- [ ] Use SWIR/local renderer for deterministic presentation expansion.
- [ ] Count output savings only when tokens were never generated by provider.

## 5. P2: local/open-weight experiments

These are not universal dependencies.

### TE-P2-01 Hidden-State Code Pruning

- [ ] SWE-Pruner-Pro-style relevance heads only where model internals are available.
- [ ] Compare against graph/range deterministic baseline before promotion.

### TE-P2-02 Resource-Wise KV Cache

- [ ] Cache stable resource/tool/skill prefixes for self-hosted models.
- [ ] Report as compute/latency optimization, not provider-billed token savings.

### TE-P2-03 TokenSkip / Learned Reasoning Pruner

- [ ] Optional LoRA/training lane for owned/open models.
- [ ] Task/model-specific evaluation required.
- [ ] No hosted-model transfer assumptions.

### TE-P2-04 Native SWIR Vocabulary

- [ ] Train/evaluate true one-token semantic operators only when tokenizer/model can be modified.
- [ ] Preserve versioned round-trip semantics and natural-language fallback.

## 6. Competitive references to preserve in design rationale

Mechanisms may be borrowed; dependencies are optional unless separately justified.

- Qwen Code: canonical tool-schema ordering / prompt-cache stability.
- Anthropic: Tool Search and provider-native context editing.
- PayPal SCOUT: sparse+dense tool retrieval and tiny meta-tool exposure at very large catalog scale.
- context-fold: fold oversized tool results before first model visibility with reversible handles.
- JetBrains observation masking: deterministic masking before paid summary generation.
- SWE-Pruner / SWE-Pruner Pro: task-aware residual code selection.
- AutoCompact / SelfCompact: trajectory/semantic compaction timing.
- Mem0: bounded multi-signal memory retrieval.
- LightMem reproduction: raw-turn retrieval can outperform summary-only constructed memory; preserve raw authority.
- GPTCache: semantic reuse inspiration, but coding reuse requires much stronger state/policy/verifier fingerprints.
- Sketch-of-Thought / TokenSkip: reasoning-token reduction research.
- OpenAI/other providers: native prompt-cache, reasoning, output and state controls when explicitly supported.

## 7. Execution waves

### TE-0 Measurement and reconciliation

- [ ] Explain >=95% of provider-visible token mass by source in paired traces.
- [ ] Reconcile all P0/P1/P2 items to canonical owners/CAP rows.
- [ ] Record dependencies, invalidation, exact recovery and verifier contracts.

### TE-1 Ingestion and constant-context loop

- [ ] TE-P0-01, 02, 03, 04, 09.

### TE-2 Prefix/cache stability

- [x] TE-P0-05 base canonicalization.
- [ ] TE-P0-06, 07, 14.

### TE-3 Tool data minimization

- [ ] TE-P0-08, 12, 13.
- [ ] TE-P1-01, 02.

### TE-4 Inference elimination

- [ ] TE-P0-10, 11.

### TE-5 Repository/memory residual minimization

- [ ] TE-P1-03, 04, 05, 06, 07.

### TE-6 Reasoning/output minimization

- [ ] TE-P1-08, 09.

### TE-7 Local/open-weight experiments

- [ ] TE-P2-01..04 only after universal lanes have measured baselines.

### TE-8 End-to-end certification

- [ ] Component A/B benchmarks.
- [ ] Frozen end-to-end coding tasks.
- [ ] Long-session trajectories.
- [ ] Provider receipts.
- [ ] Quality/verifier non-inferiority.
- [ ] Security/invalidation/recovery tests.
- [ ] Confidence intervals and regression thresholds.
- [ ] Workload-specific promotion, rollback and claim boundary.

## 8. Promotion targets

Targets are engineering gates, not current product claims.

- Repeated deterministic exact-hit task: **0 provider tokens** where verifier-gated skip is valid.
- Easy/warm task: **300-1,500 provider-visible tokens** stretch target.
- Easy frontier-needed task: **1K-3K**.
- Normal coding-agent task: **3K-8K** where irreducible evidence fits.
- Hard scoped task: **5K-15K** before necessity-backed overflow.
- Avoidable input reduction: >=85% floor, >=95% stretch on eligible workloads.
- Avoidable output reduction: >=85% floor, >=95% stretch on eligible workloads.
- Unexplained envelope overflow: 0.
- Quality/verifier success: non-inferior to frozen baseline.

Component savings percentages are non-additive. Prompt-cache savings are not context-capacity reduction. Post-generation truncation is not provider-output savings.

## 9. Metrics required before claiming success

- fresh input tokens;
- cached input read/write tokens where provider reports them;
- output tokens;
- reasoning tokens;
- tool-schema tokens;
- tool-output tokens;
- repository/context tokens;
- active vs superseded/stale evidence tokens;
- provider calls and tool calls;
- recovery recalls and recall tokens;
- cache-break count/reason;
- inference-skip hit rate;
- prevented false semantic hits;
- macro/workflow reuse rate;
- verifier success/regression rate;
- exact recovery success;
- invalidation/stale-cache failures;
- latency per successful task;
- provider cost per successful task;
- provider tokens per new verified information.

Primary KPI:

`Verified Provider Cost / Successful Task`

## 10. Safety and architecture constraints

- No lossy transform of security policy, user constraints, exact edit anchors or verifier-critical evidence without an explicit fidelity contract.
- Exact raw artifacts remain recoverable before bodies are evicted.
- Semantic caches fail closed.
- Inference skip requires complete state/policy/verifier fingerprint.
- Raw memory remains authority.
- New competitive names do not authorize duplicate stores, indexes, routers, ledgers or public APIs.
- No-expansion is mandatory under the target tokenizer.
- Provider-native features are capability-gated and portable fallbacks remain available.
- Provider-billed savings claims require provider-observed paired receipts.

## 11. Continuation order

When resuming work, do **not** start from the most exciting research feature. Continue in this order:

1. TE-0 attribution + reconciliation.
2. TE-P0-01 Pre-Model Ingestion Fold Gate.
3. TE-P0-02/03/04/09 constant-context history and delta path.
4. TE-P0-06/07 cache diagnostics + break-even policy.
5. TE-P0-14 OpenAI cache modernization behind capability gates.
6. TE-P0-08 query pushdown.
7. TE-P0-10/11 verified inference skip + macro compiler.
8. TE-P0-12/13 provider-native adapters.
9. P1 retrieval/memory/reasoning/output residual work.
10. P2 local/open-weight experiments only after universal paths are measured.
11. TE-8 provider-receipt end-to-end certification.

Do not manually rerun equivalent GitHub Actions for the same SHA/workflow/input while an equivalent run is queued or in progress. Continue independent implementation work instead.
