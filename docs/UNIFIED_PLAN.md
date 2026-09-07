# Syntavra Unified Plan

Syntavra remains one product and one evidence boundary: **0.0.1 / pre-release**.

## Current product role

Syntavra is a local-first **Verified Experience-Compiling Inference Operating System** for existing AI coding tools.

The old “token/context optimizer” identity remains a valid foundation, but it is no longer the full target. The governing objective is:

> Minimize provider inference required per **verified successful software-engineering task**, while preserving correctness, security, exact recovery and external-evidence boundaries.

The immediate competitive architecture is the **Context Execution Compiler**: decide what context/work is necessary before compression, suppress replay and duplicate work, compile repository/tool/instruction state into bounded evidence, and send provider inference only the residual uncertainty.

The token-economy objective is stronger than compression alone:

`do not create -> do not retrieve -> do not resend -> do not reason again -> do not generate -> compact only the irreducible residual`

For eligible repeated deterministic work, the target is not “very few tokens”; it is a verifier-gated **zero-provider-token** execution path.

## Current planning authority

Read in this order for new development:

1. `docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md` — volatile operational continuation state for the admitted post-280 track.
2. `docs/plans/HYPEREFFICIENCY_MASTER_ROADMAP_V6.md` — current append-only long-form roadmap.
3. `docs/plans/SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md` — reconciliation overlay for the current context/token/tool/retrieval architecture; it creates no parallel CAP namespace.
4. `docs/plans/SYNTAVRA_TOKEN_ECONOMY_COMPETITIVE_GAP_V1.md` — competitive research overlay and evidence-derived token-economy gaps.
5. `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` — active TODO/continuation order for implementing those gaps.
6. `contracts/python/token-economy-competitive-gap-v1.json` — machine-readable competitive-gap registry.
7. `contracts/python/token-economy-execution-backlog-v1.json` — machine-readable execution/status/next-order registry.
8. `contracts/python/hyperefficiency-roadmap-v1.json` — machine-readable CAP-0281..CAP-1648 admission/state map.
9. `docs/research/hyperefficiency/` — lossless rationale/source-provenance snapshots, not execution authority.
10. Existing Python-first completion registries/certificates — authority for the frozen <=280 boundary and Rust retirement state.

Historical token-saver plans remain provenance and implementation foundations. They do not override the authority order above.

## Preserved completion boundary

The new roadmap does not reopen prior certified work:

- Python capabilities 236-270 and 276-280 remain closed/certified under their existing authorities.
- Capabilities 271-275 remain deferred Rust-transition work.
- `rust_resume_allowed=false`.
- `rust_retired=true`.
- Rust production promotion remains 174/245 with 71 remaining.

## New roadmap admission boundary

The admitted roadmap now combines the original HyperEfficiency corpus plus Token Elimination v10:

```text
HE-0001..HE-0339   → V3
HE-0340..HE-0654   → V4
HE-0655..HE-0982   → V5
HE-0983..HE-1284   → V6
HE-1285..HE-1368   → Token Elimination v10

canonical number = 280 + HE number

HE-0001 → CAP-0281
HE-1284 → CAP-1564
HE-1285 → CAP-1565
HE-1368 → CAP-1648
```

Every imported capability begins as `ADMITTED_RECONCILIATION`. That is a planning state, not evidence that implementation is absent.

Before implementation, classify each candidate as:

```text
EXISTS | HARDEN | UNIFY | NEW | CERTIFY | EXTERNAL | DEFERRED
```

## Optimization priority

```text
UNDERSTAND
→ DO NOT CREATE UNNECESSARY STATE
→ EXCLUDE IRRELEVANT STATE
→ SCOPE TO THE MINIMUM WORKSPACE
→ DEDUPLICATE / SUPERSEDE
→ REUSE EXPERIENCE / CACHE / ARTIFACTS
→ PROVE WHAT CHANGED
→ RETRIEVE ONLY REQUIRED EVIDENCE
→ EXTERNALIZE LARGE RECOVERABLE PAYLOADS
→ PROJECT / AGGREGATE / DELTA
→ ADMIT THE MINIMUM SUFFICIENT CONTEXT
→ SOLVE DETERMINISTICALLY
→ VERIFY
→ SKIP INFERENCE WHEN A COMPLETE VERIFIED FINGERPRINT MATCHES
→ LOCAL SPECIALIST
→ CHEAP MODEL
→ FRONTIER ONLY FOR RESIDUAL UNCERTAINTY
→ VERIFY AGAIN
→ LEARN / COMPILE A REUSABLE ABSTRACTION OR MACRO
→ COMPRESS ONLY THE IRREDUCIBLE RESIDUAL
```

Compression and terse serialization occur **after** exclusion, deduplication and admission. Saving tokens by sending a smaller version of something that should never have been sent is still waste.

## Primary metrics

- Verified Provider Cost / Successful Task
- Provider Tokens / Verified Successful Task
- Provider Tokens / New Verified Information
- Frontier Dependency Ratio
- Inference-Free Task Fraction
- Repeated Work Reuse
- Reacquisition Waste
- Duplicate Read / Command Waste
- MCP Schema Tokens / Turn
- Tool Output Tokens / Turn
- Active vs Superseded Evidence Tokens
- Reasoning Tokens / Successful Task
- Output Tokens / Successful Task
- Prompt Cache Break Count / Cause
- Inference-Skip Hit Rate / Prevented False Hits
- Exact Recovery Coverage
- Cache Invalidation Correctness
- Verification Coverage

No provider-cost superiority claim is valid without provider-observed receipts under frozen equivalent task/verifier conditions.

## Integrated optimization surfaces

- requirements/spec compilation;
- execution/state ledgers;
- verifier-first execution and verifier-of-verifier checks;
- persistent repository digital twin and query compiler;
- Tree-sitter/LSP/ripgrep repository intelligence fusion;
- evidence graph, program slicing and semantic traces;
- context admission, replay suppression, virtual memory, semantic delta and safe compression;
- content-addressed reads and exact/semantic/work/proof caching;
- artifact-first tool output, pre-model fold gates, failure evidence pinning and streaming compaction;
- active-context supersession, no-change elision, causal history skeletons and constant-context tool loops;
- field projection, query pushdown, aggregation and delta tool responses before provider visibility;
- test selection and test-output compaction;
- MCP schema budgeting, lazy tool discovery, hybrid sparse+dense tool retrieval and schema-on-demand loading;
- provider-native tool search/context editing when capability-gated and benchmark-superior;
- stable prompt-prefix/tool-schema canonicalization, cache-break attribution and cache break-even eviction;
- scoped AGENTS.md compilation with precedence/provenance;
- hierarchical experience, plan/strategy/failure memory;
- raw + constructed dual-track memory with multi-signal bounded reranking;
- deterministic workflow compilation, workflow skill/macro compilation and programmatic tool orchestration;
- verifier-gated inference skipping for complete state/policy/verifier fingerprints;
- subagent context firewalls and parallel-work deduplication;
- tokenizer-aware serialization and optional TOON/LLMLingua-family experiments;
- deterministic masking/supersession before any paid LLM summarization;
- phase/trajectory-aware compaction;
- local task-aware residual code pruning after exact graph/range retrieval;
- local specialists, reasoning-sketch/reasoning-effort governance and frontier-last routing;
- dynamic output contracts, Semantic Wire IR and semantic-novelty enforcement before generation;
- optional gateway adapters such as LiteLLM/OpenRouter/9Router;
- provider-assisted compilation/compaction/cache controls where explicitly supported;
- optional self-hosted KV/prefix/resource-wise serving optimizations;
- optional hidden-state code pruning, TokenSkip-style reasoning pruning and native SWIR vocabulary for owned/open models;
- adaptive retrieval and token-per-success policy optimization behind shadow/rollback gates;
- SignalBench/provider receipts and cost attribution;
- exact recovery, security, provenance, TTL, invalidation and rollback.

## Token Economy continuation backlog

`docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` is the active operational list. Current groups are:

### P0: eliminate provider work before compression

1. Pre-Model Ingestion Fold Gate.
2. Active Context Supersession Graph.
3. Useless / No-Change Result Elision.
4. Causal History Skeleton + Constant-Context Tool Loop.
5. Stable Tool Schema Canonicalization.
6. Prompt Cache Break Detector.
7. Cache Break-Even Eviction Governor.
8. Tool Result Query Pushdown.
9. Delta Tool Response Protocol.
10. Verifier-Gated Inference Skip Cache.
11. Workflow Skill / Macro Compiler.
12. Provider-Native Context Editing Adapter.
13. Provider-Native Tool Search Adapter.
14. OpenAI Prompt Cache Modernization.

`TE-P0-05 Stable Tool Schema Canonicalization` has a first implementation/hardening commit at `8d78cfc0b66d01eea5f01b1aec0ad255ef5b692d`; provider-observed savings certification remains open.

### P1: residual retrieval/memory/reasoning/output minimization

- Hybrid Tool Search (BM25 + dense + RRF).
- Multi-Level Tool Detail (L0/L1/L2).
- Task-Aware Residual Code Pruner.
- Deterministic Mask/Supersede Before Summary.
- Phase/Trajectory-Aware Compaction.
- Dual-Track Raw + Constructed Memory.
- Multi-Signal Memory Reranker.
- Reasoning Sketch Governor.
- Dynamic Output Contract + Semantic Novelty Gate.

### P2: local/open-weight experiments

- Hidden-State Code Pruning.
- Resource-Wise KV Cache.
- TokenSkip / Learned Reasoning Pruner.
- Native SWIR Vocabulary.

P2 is intentionally behind measured universal paths. Exotic local-model tricks do not outrank deleting unnecessary provider work at ingestion.

## Token Economy execution waves

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

Continuation order is defined by the backlog, not by whichever research feature happens to look most entertaining that day.

## Engineering promotion targets

These are targets/gates, not current measured Syntavra product claims:

- verified exact repeated deterministic task: **0 provider tokens** where inference-skip proof is complete;
- easy/warm eligible task: **300-1,500** provider-visible tokens stretch target;
- easy task still requiring frontier reasoning: **1K-3K**;
- normal coding-agent task: **3K-8K** where irreducible evidence fits;
- hard but scoped task: **5K-15K** before necessity-backed overflow;
- avoidable input reduction: >=85% floor, >=95% stretch on eligible workloads;
- avoidable output reduction: >=85% floor, >=95% stretch on eligible workloads;
- unexplained provider-envelope overflow: 0;
- task/verifier/security quality: non-inferior to frozen baseline.

Component savings percentages are non-additive. Prompt caching reduces repeated processing/cost where supported but is not itself context-capacity reduction. Post-generation truncation is not provider-output savings.

## Context Execution Compiler component groups

The requested architecture names are governed by `docs/plans/SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md`. The important boundary is:

- Syntavra-owned core policy: Context Replay Breaker, Token Budget Governor, Context Admission Controller, state/cache/dedup, artifact/test pipeline, MCP Schema Governor, AGENTS.md Compiler, lifecycle/context firewall, token-cost-aware graph traversal, adaptive retrieval and Token-per-Success Optimizer.
- low-level primitives: Tree-sitter, LSP and ripgrep;
- optional adapters/competitive references: Graphify, Serena, Aider Repo-Map, RTK, Ponytail, Caveman, Headroom, 9Router, LiteLLM and OpenRouter;
- fidelity-gated experimental transforms: LLMLingua, LLMLingua-2, LongLLMLingua and TOON;
- host/provider-gated surfaces: Native Codex Compaction and provider-specific reasoning/cache controls.

External project names do not authorize duplicate internal owners.

## Non-destructive implementation rule

New roadmap names do not authorize duplicate infrastructure. Reuse canonical owners first. Public-surface growth defaults to zero. A new store/router/index/service requires an explicit architecture justification and verifier.

A transform must also satisfy the **no-expansion rule**: if it is not smaller under the target tokenizer, or cannot meet its fidelity/recovery contract, pass the original through unchanged.

Raw/exact evidence remains authority before any summary, constructed memory or lossy view. Deterministic drop/dedup/supersession/masking/handle/delta paths must be attempted before paid LLM summarization.

## CI discipline

For the same SHA/workflow/input, never create duplicate active Actions. Track an existing queued/in-progress `run_id` and continue independent work while CI runs.
