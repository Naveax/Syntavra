# Syntavra Unified Plan

Syntavra remains one product and one evidence boundary: **0.0.1 / pre-release**.

## Current product role

Syntavra is a local-first **Verified Experience-Compiling Inference Operating System** for existing AI coding tools.

The old “token/context optimizer” identity remains a valid foundation, but it is no longer the full target. The governing objective is:

> Minimize provider inference required per **verified successful software-engineering task**, while preserving correctness, security, exact recovery and external-evidence boundaries.

The immediate competitive architecture is the **Context Execution Compiler**: decide what context/work is necessary before compression, suppress replay and duplicate work, compile repository/tool/instruction state into bounded evidence, and send provider inference only the residual uncertainty.

## Current planning authority

Read in this order for new development:

1. `docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md` — current operational continuation state; now points into admitted post-280 HyperEfficiency work.
2. `docs/plans/HYPEREFFICIENCY_MASTER_ROADMAP_V6.md` — current append-only long-form roadmap.
3. `docs/plans/SYNTAVRA_CONTEXT_EXECUTION_COMPILER_V1.md` — reconciliation overlay for the current context/token/tool/retrieval architecture; it creates no parallel CAP namespace.
4. `contracts/python/hyperefficiency-roadmap-v1.json` — machine-readable CAP-0281..CAP-1648 admission/state map.
5. `docs/research/hyperefficiency/` — lossless rationale/source-provenance snapshots, not execution authority.
6. Existing Python-first completion registries/certificates — authority for the frozen <=280 boundary and Rust retirement state.

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
→ EXCLUDE IRRELEVANT STATE
→ REUSE EXPERIENCE / CACHE / ARTIFACTS
→ PROVE WHAT CHANGED
→ ADMIT THE MINIMUM SUFFICIENT CONTEXT
→ SOLVE DETERMINISTICALLY
→ VERIFY
→ LOCAL SPECIALIST
→ CHEAP MODEL
→ FRONTIER ONLY FOR RESIDUAL UNCERTAINTY
→ VERIFY AGAIN
→ LEARN AN ABSTRACTION
```

Compression and terse serialization occur **after** exclusion, deduplication and admission. Saving tokens by sending a smaller version of something that should never have been sent is still waste.

## Primary metrics

- Verified Provider Cost / Successful Task
- Provider Tokens / Verified Successful Task
- Frontier Dependency Ratio
- Inference-Free Task Fraction
- Repeated Work Reuse
- Reacquisition Waste
- Duplicate Read / Command Waste
- MCP Schema Tokens / Turn
- Verification Coverage
- Exact Recovery Coverage
- Cache Invalidation Correctness

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
- artifact-first tool output, failure evidence pinning and streaming compaction;
- test selection and test-output compaction;
- MCP schema budgeting, lazy tool discovery and schema-on-demand loading;
- scoped AGENTS.md compilation with precedence/provenance;
- hierarchical experience, plan/strategy/failure memory;
- deterministic workflow compilation and programmatic tool orchestration;
- subagent context firewalls and parallel-work deduplication;
- tokenizer-aware serialization and optional TOON/LLMLingua-family experiments;
- local specialists, reasoning-effort governance and frontier-last routing;
- optional gateway adapters such as LiteLLM/OpenRouter/9Router;
- provider-assisted compilation/compaction where explicitly supported;
- optional self-hosted KV/prefix/serving optimizations;
- adaptive retrieval and token-per-success policy optimization behind shadow/rollback gates;
- SignalBench/provider receipts and cost attribution;
- exact recovery, security, provenance, TTL, invalidation and rollback.

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

## CI discipline

For the same SHA/workflow/input, never create duplicate active Actions. Track an existing queued/in-progress `run_id` and continue independent work while CI runs.
