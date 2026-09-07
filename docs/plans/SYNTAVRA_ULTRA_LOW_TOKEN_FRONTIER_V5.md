# Syntavra Ultra-Low Token Frontier V5

Status: RESEARCH-ADMITTED / EXECUTION-OVERLAY / EXTENDS V4 / NO NEW CAP NAMESPACE  
Date: 2026-09-07  
Authority: HyperEfficiency Master Roadmap V6 + Token Economy Backlogs V1..V5 + Ultra-Low Frontiers V1..V4

## Objective

V5 attacks the remaining token floor through zero-provider memory operations, reacquisition-aware retention, adaptive communication media and a global marginal-value token allocator.

Governing order:

`avoid LLM memory work -> retain only when future reacquisition is dearer -> choose the cheapest faithful communication medium -> auction scarce tokens by marginal verified value -> entropy-code residual wire forms -> optionally reversible-minify source -> verify -> search lower again`

The product objective remains:

`minimum verified provider work = irreducible information + irreducible uncertainty`

## Research floor-search bands

These are aggressive probes, not current product claims or universal guarantees.

| Workload | V4 probe | V5 research probe |
|---|---:|---:|
| verified repeated deterministic | 0 | 0 |
| easy / warm / highly reusable | 0-50 | 0-25 |
| easy but frontier reasoning required | 50-250 | 25-150 |
| normal scoped coding-agent task | 250-1,000 | 150-750 |
| hard but well-scoped coding task | 1,000-3,000 | 750-2,500 before necessity-backed overflow |

Verified Token Floor Search may stop above these bands when verifier/security/exact-recovery or end-to-end economics require it.

## New and hardened mechanisms

### U5-P0-01 Zero-Token Memory Engine

Classification: `HARDEN/UNIFY`  
Owners: raw memory authority + memory retrieval + repository/state indexes + EvidenceStore

Memory ingest, indexing, temporal/entity/symbol linking, deduplication, retention metadata and deterministic retrieval should require no provider call by default.

Rules:
- raw exact events remain authority;
- constructed summaries are optional accelerators, never the only truth source;
- deterministic graph/index maintenance precedes any learned or paid summarization;
- provider tokens for memory maintenance are zero unless a separately justified semantic transform is promoted;
- final reasoning may still use a model when irreducible uncertainty remains.

### U5-P0-02 Reacquisition Tax Governor

Classification: `NEW/HARDEN`  
Owners: ContextAdmissionController + AdaptiveContextPolicy + Reacquisition accounting + TokenPerSuccessOptimizer

A context object must not be dropped merely because its current carry cost is non-zero. Estimate future retrieval/re-read/reconstruction cost.

Core decision:

`retain when expected_reacquisition_cost > expected_carry_cost`, subject to correctness/security/invalidation constraints.

Measure:
- context tokens removed;
- later recalls/re-reads caused by that removal;
- tool/provider tokens spent reacquiring;
- latency/cost from reacquisition;
- net verified cost per successful task.

### U5-P0-03 Future-Reuse Retention Predictor

Classification: `HARDEN/SHADOW_FIRST`  
Owners: ContextValuePredictor + SemanticStateMachine + Reacquisition accounting

Estimate:

`retention_value = future_use_probability * reacquisition_cost - carry_cost`

Mandatory user/security/failure/verifier evidence is pinned outside learned retention decisions. The predictor starts in shadow mode and is trained/evaluated with counterfactual drop/reacquisition receipts.

### U5-P0-04 Communication Medium Router

Classification: `NEW/HARDEN`  
Owners: SWIR + subagent runtime + local runtime + ProviderGateway

For every agent-to-agent or state handoff, choose the cheapest faithful medium supported by both endpoints:

- silence;
- stable handle / typed receipt;
- SWIR / compact structured text;
- natural language;
- embedding / hidden-state / KV only for compatible local/owned models.

Routing optimizes verified end-to-end cost, not nominal payload size. Hosted-provider paths cannot claim latent/KV savings unless provider-visible work actually falls.

### U5-P0-05 Global Token Budget Auction

Classification: `NEW/HARDEN`  
Owners: ProviderTokenEnvelope + ContextAdmissionController + ReasoningEffortGovernor + OutputGovernor + TokenPerSuccessOptimizer

Treat provider-visible token budget as a shared scarce resource across retrieval, instructions/context, tool schemas/results, reasoning and output.

Allocate each marginal token to the lane with the highest estimated increase in verified success or new verified information per token, while preserving hard minima and mandatory evidence.

Promotion gates:
- hard user/security/exact/verifier minima cannot be auctioned away;
- allocation is workload/model/provider specific;
- all retries/recalls/fallbacks count;
- compare against fixed per-lane budgets under identical frozen tasks/verifiers.

### U5-P1-01 Entropy-Coded SWIR V2

Classification: `HARDEN/EXPERIMENTAL`  
Owners: SWIR + TokenizerAwareSerializer

Optimize frequent opcodes, semantic macros, field identifiers and receipts against observed workload frequency and the target tokenizer.

Principle:

`minimize sum(frequency(symbol) * tokenizer_cost(symbol)) + codebook_cost`

No-expansion, exact-recovery, stable-version and fallback requirements remain mandatory. Hosted paths use this only where grammar/codebook cost is amortized and cache-stable.

### U5-P1-02 Reversible Source Minification

Classification: `EXPERIMENTAL/P2`  
Owners: repository intelligence + edit compiler + source-map verifier

Evaluate AST-safe reversible removal/normalization of comments, whitespace and other non-semantic lexical mass before provider visibility.

Requirements:
- exact source map back to canonical source;
- symbols and edit targets preserve identity;
- user-requested comments/docs and semantically relevant formatting are never silently removed;
- parser/formatter/tests and task verifier must remain non-inferior;
- if total end-to-end tokens increase through repair/re-read, the transform is rejected.

### U5-P1-03 Reacquisition-Aware Compaction Scheduler

Classification: `HARDEN`  
Owners: phase-aware compaction + ContextAdmissionController + prompt-cache economics

Compaction timing must consider both current context pressure and expected future reacquisition/cache rewrite cost. Delay or partially retain context when aggressive pruning would create a larger downstream bill.

## Execution waves

```text
TE-U30  zero-token memory engine
TE-U31  reacquisition tax + future-reuse retention
TE-U32  communication medium router
TE-U33  global token budget auction
TE-U34  entropy-coded SWIR v2
TE-U35  reversible source minification + reacquisition-aware compaction experiments
TE-U36  compound V5 floor certification and next downward probe
```

V1..V4 waves remain prerequisites. Deterministic pre-model elimination, constant-context history, tool-chain fusion, query pushdown, inference skip, structural editing, adaptive retrieval, early stop and strategic silence still outrank lossy/learned compression.

## Measurements

Track at minimum:
- provider tokens spent on memory maintenance;
- memory retrieval provider calls avoided;
- reacquisition tokens/calls/latency after context removal;
- retained-context carry cost versus later reacquisition cost;
- communication medium chosen and provider-visible payload avoided;
- token allocation by lane and marginal verified gain estimate;
- SWIR codebook amortization and tokenizer cost;
- minification token reduction, repair/readback cost and verifier delta;
- final provider tokens and verified cost per successful task.

## Hard invariants

1. Raw exact memory/evidence remains authority.
2. Zero-token memory may not hide provider calls in maintenance workers.
3. Reacquisition cost is counted end-to-end.
4. Mandatory evidence cannot be dropped by retention or budget policies.
5. Local latent/KV communication is not a hosted-provider token claim by itself.
6. Entropy coding obeys target-tokenizer no-expansion and versioned recovery.
7. Source minification is reversible and verifier-gated.
8. Every lower band remains a research probe until paired provider receipts certify it.
9. A higher safe empirical floor overrides the requested target.
10. After certification, Verified Token Floor Search is allowed to probe lower again.
