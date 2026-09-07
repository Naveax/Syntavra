# Syntavra Token Economy Execution Backlog V2

Status: ACTIVE CONTINUATION BACKLOG / EXTENDS V1 / RECONCILIATION-FIRST / NO NEW CAP NAMESPACE  
Date: 2026-09-07

## 1. Authority

V2 **extends** `SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md`; it does not discard any open V1 P0/P1/P2 task.

New research/engineering authority:

- `docs/plans/SYNTAVRA_ULTRA_LOW_TOKEN_FRONTIER_V1.md`
- `contracts/python/ultra-low-token-frontier-v1.json`
- `contracts/python/token-economy-execution-backlog-v2.json`

Existing canonical owners must be reused before any new owner is created.

## 2. Revised execution priority

Highest ROI continuation order:

1. **TE-P0-01 Pre-Model Ingestion Fold Gate**.
2. **TE-P0-02/03/04/09** supersession, no-change elision, causal history and delta constant-context loop.
3. **U-P0-01 Tool-Chain Fusion / Programmatic Tool Calling** so intermediate tool results never become provider history when a deterministic fused transaction is valid.
4. **TE-P0-08 Tool Result Query Pushdown** so structured bulk is filtered/aggregated before provider visibility.
5. **TE-P0-10 + U-P0-02** verifier-gated inference skip and zero-inference answer synthesis.
6. **TE-P0-11 + U-P0-03** workflow macro compiler and verified micro-agent macro runtime.
7. **TE-P0-06/07/14** cache-break detection, break-even pruning and current provider cache controls.
8. **TE-P0-12/13** provider-native context editing/tool search where end-to-end superior.
9. **U-P0-04** current-truth contradiction collapse as a hardening of supersession.
10. Residual learned/representation optimizers only after the deterministic pipeline is measured.

`TE-P0-05 Stable Tool Schema Canonicalization` remains implemented/hardened at `8d78cfc0b66d01eea5f01b1aec0ad255ef5b692d`; provider savings certification remains open.

## 3. New ultra-low TODOs

### U-P0-01 Tool-Chain Fusion / Programmatic Tool Calling

- [ ] Reconcile with existing `programmatic-execution-v1` owner and tool pipeline.
- [ ] Detect deterministic multi-tool chains safe to fuse.
- [ ] Execute filtering/aggregation/intermediate state locally or provider-native where supported.
- [ ] Keep intermediate tool results out of model history.
- [ ] Persist exact intermediate artifacts only when recovery/audit requires them.
- [ ] Return a bounded final receipt.
- [ ] Benchmark against sequential tool calls; do not enable when fusion adds cost on short chains.

### U-P0-02 Zero-Inference Answer Synthesis

- [ ] Extend TE-P0-10 exact verified hit path with deterministic user-facing rendering.
- [ ] Require complete state/policy/verifier fingerprint.
- [ ] Render only evidence-backed fields; no model-authored embellishment.
- [ ] Record provider_call_avoided receipt.

### U-P0-03 Verified Micro-Agent Macro Runtime

- [ ] Extend TE-P0-11 compiled workflows into small local parameterized executors.
- [ ] Revalidate tool/environment/policy/verifier versions before execution.
- [ ] Fall back to inference on unsupported/ambiguous state.
- [ ] Measure inference-free task fraction by task family.

### U-P0-04 Current-Truth Contradiction Collapse

- [ ] Extend TE-P0-02 with explicit current-truth identity.
- [ ] Keep old conflicting state as lineage handles only.
- [ ] Reactivate historical bodies only for explicit historical/comparison tasks.

### U-P1-01 Action-Preserving Observation Compressor

- [ ] Implement behind deterministic fold/mask/delta pipeline, never before it.
- [ ] Define next-action preservation benchmark using frozen agent/tool state.
- [ ] Optimize length only among action-preserving candidates.
- [ ] Keep exact raw observation recoverable.
- [ ] Include extra recovery calls in total-token accounting.
- [ ] Promote only when verifier success is non-inferior.

### U-P1-02 Multi-Rubric Residual Code Pruner

- [ ] Split semantic-evidence and dependency-support scoring.
- [ ] Use AST/graph edges for dependency-support labels/verifiers.
- [ ] Preserve exact edit anchors and failure/verifier lines.
- [ ] Run only after graph/symbol/range retrieval.
- [ ] Compare against deterministic-only and single-rubric pruner baselines.

### U-P1-03 Cross-Lingual Token Arbitrage

- [ ] Generate candidate forms: original, compact same-language, compact English, structured task, SWIR.
- [ ] Preserve literals/paths/code/identifiers/quoted exact text byte-exactly.
- [ ] Semantic/constraint equivalence gate each rewrite.
- [ ] Tokenize candidates with the actual target-provider tokenizer.
- [ ] Choose smallest passing representation; otherwise original.
- [ ] Track savings separately by language and provider.

### U-P1-04 Context Value Predictor

- [ ] Train/evaluate only in shadow mode first.
- [ ] Predict marginal decision/verifier value, not generic semantic similarity.
- [ ] Include novelty, dependency, freshness, trust, recall and action-sensitivity signals.
- [ ] Mandatory/user/security/exact/verifier evidence is never droppable by predictor score.

### U-P1-05 Step-Level Reasoning Budgeter

- [ ] Assign reasoning lease per decision step.
- [ ] Use native provider effort/verbosity controls first.
- [ ] Escalate on uncertainty/verifier failure; contract after deterministic resolution.
- [ ] Record reasoning tokens per step when provider receipts expose them.

### U-P1-06 Provider / Tokenizer Arbitrage Router

- [ ] Estimate prepared-token count per allowed route.
- [ ] Include price, cache, expected reasoning/output, latency and historical verifier success.
- [ ] Optimize expected verified cost, not nominal token count.
- [ ] Respect privacy/security/provider constraints before cost.

### U-P1-07 Learned Tool Result Visibility Policy

- [ ] Learn preview/lease/range defaults by tool-result family from recall behavior.
- [ ] Separate success and failure visibility policies.
- [ ] Keep exact recovery, security and failure pinning hard-coded.
- [ ] Roll back if learned policy increases recall turns or verifier regressions.

## 4. Ultra-low waves

```text
TE-U0  baseline/reconciliation for ultra-low bands
TE-U1  tool-chain fusion + intermediate-result elimination
TE-U2  action-preserving residual observations
TE-U3  multi-rubric residual code pruning
TE-U4  cross-lingual / representation / tokenizer arbitrage
TE-U5  context-value prediction + step-level reasoning budgets
TE-U6  zero-inference synthesis + verified micro-agent macros
TE-U7  compound trajectory certification with provider receipts
```

## 5. Revised stretch targets

These are not current product claims.

```text
verified repeated deterministic: 0 provider tokens
easy/warm eligible:             0-500 stretch
easy frontier-needed:           500-2,000 stretch
normal scoped coding:           2,000-5,000 stretch
hard scoped coding:             4,000-8,000 before necessity-backed overflow
eligible easy avoidable:        >=97% target / >=99.5% stretch
normal scoped avoidable:        >=95% target / >=98% stretch
```

The older V1 targets remain the base promotion bands until the ultra-low bands are independently certified.

## 6. Measurement additions

Add to TE-0 / provider receipts:

- intermediate tool-result tokens prevented by fusion;
- fused-chain vs sequential provider calls;
- next-action preservation rate;
- action divergence caused by compression;
- multi-rubric keep/drop attribution;
- translation/representation candidate token counts;
- semantic rewrite fallback rate;
- context-value predictor prevented-drop count for mandatory evidence;
- reasoning tokens per step;
- expected-vs-realized verified routing cost;
- zero-inference answer count;
- micro-agent macro success/rollback rate.

## 7. Completion rule

V2 is complete only when V1 open work plus every promoted U-P0/U-P1 item has a canonical owner, invalidation/recovery contract, verifier, workload-specific benchmark and provider-observed evidence for any provider-saving claim.

The goal is not to produce the smallest prompt. The goal is the smallest **verified amount of provider work** needed to complete the task.
