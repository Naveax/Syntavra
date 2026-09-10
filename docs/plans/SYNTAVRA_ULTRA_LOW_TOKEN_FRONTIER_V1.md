# Syntavra Ultra-Low Token Frontier V1

Status: RESEARCH-ADMITTED / EXECUTION-OVERLAY / RECONCILIATION-FIRST / NO NEW CAP NAMESPACE  
Date: 2026-09-07  
Authority: HyperEfficiency Master Roadmap V6 + Context Execution Compiler + Token Economy Backlog

## 1. Objective

This overlay pushes Syntavra beyond the existing 3K-8K mature provider-envelope target for eligible workloads. The new objective is not merely stronger compression. It is to reduce or eliminate provider work by fusing tool chains, preserving only decision-relevant observations, choosing the cheapest faithful representation, budgeting reasoning per step, and turning verified repeated behavior into local execution.

The frontier rule is:

`eliminate -> fuse -> project -> preserve action -> choose cheapest faithful representation -> budget reasoning -> generate only novel output -> verify`

No item in this document creates a new CAP namespace. Reconcile against existing owners first.

## 2. Ultra-low engineering targets

Targets are workload-scoped promotion gates, not current measured product claims.

| Workload | Base target | Ultra-low stretch target |
|---|---:|---:|
| verified repeated deterministic | 0 provider tokens | 0 |
| easy / warm / highly reusable | 300-1,500 | 0-500 |
| easy but frontier reasoning required | 1K-3K | 500-2K |
| normal scoped coding-agent task | 3K-8K | 2K-5K |
| hard but well-scoped coding task | 5K-15K | 4K-8K before necessity-backed overflow |

Additional stretch gates:

- eligible easy/warm avoidable-token reduction: >=97%, stretch >=99.5%;
- normal scoped coding avoidable-token reduction: >=95%, stretch >=98%;
- provider calls avoided are tracked separately from compressed-token savings;
- quality, verifier success and security must be non-inferior to the frozen baseline;
- irreducible exact/user/security/verifier evidence may exceed the stretch band only with necessity proof.

## 3. New and hardened mechanisms

### U-P0-01 Tool-Chain Fusion / Programmatic Tool Calling

Classification: `UNIFY/HARDEN`  
Canonical owners: Programmatic Execution + tool pipeline + ProviderGateway

Multiple small model/tool/model round trips should be fused into one local or provider-native executable transaction when dependencies are deterministic and policy permits it.

Target shape:

`model -> local/programmatic plan -> tool A -> tool B -> filter/aggregate -> verifier -> compact final receipt -> model`

Intermediate tool results must not enter provider history unless an explicit recovery/decision need requires them.

Promotion gates:

- deterministic dependency ordering;
- side-effect/security policy validation;
- bounded final receipt;
- exact artifacts for recoverable intermediate evidence;
- end-to-end benchmark must beat ordinary sequential calls, since short single-call workflows can become more expensive when fused.

### U-P0-02 Zero-Inference Answer Synthesis

Classification: `HARDEN/UNIFY`  
Canonical owners: Verifier-Gated Inference Skip Cache + OutputGovernor + SWIR renderer

When exact state, macro execution and verifier evidence fully determine the answer, render the user-facing result locally without a frontier call.

A valid zero-inference answer requires:

- complete task/state/policy/verifier fingerprint;
- deterministic result schema;
- verified execution or exact verified cache hit;
- local renderer limited to evidence-backed presentation;
- no invented explanation or facts.

### U-P0-03 Verified Micro-Agent Macro Runtime

Classification: `HARDEN/UNIFY`  
Canonical owners: Macro Factory + deterministic workflow compiler + local specialists

Repeated verified subgraphs should become small parameterized local micro-agents/macros. Frontier inference chooses or creates a workflow only when the existing macro cannot safely execute the task.

### U-P0-04 Current-Truth / Contradiction Collapse

Classification: `HARDEN`  
Canonical owners: Active Context Supersession Graph + semantic state

Conflicting historical state should collapse to current truth + provenance/lineage handles. Old contradictory bodies remain recoverable but not active unless the task explicitly requires historical comparison.

## 4. Learned residual reducers

These run only after deterministic exclusion, deduplication, supersession, exact range selection and folding.

### U-P1-01 Action-Preserving Observation Compressor

Research reference: CoACT (`THU-Agent/CoACT`, arXiv:2607.02911).

CoACT reframes fidelity from textual similarity to **next-action preservation**. Its published SWE-bench Verified evaluation reports observations as a large share of agent tokens and reports about 33% average total-token reduction while keeping task-solving effectiveness close to the uncompressed agent.

Syntavra adaptation:

- compressor sees only residual tool observations after deterministic folding;
- train/evaluate with next-action preservation + length reward;
- require verifier non-inferiority and bounded recovery calls;
- exact raw observation remains in EvidenceStore;
- if compressed observation changes the required next action or increases recovery work, fall back to deterministic receipt/raw evidence.

This is an optional learned adapter, not the authority for exact evidence.

### U-P1-02 Multi-Rubric Residual Code Pruner

Research reference: LaMR, arXiv:2605.15315.

LaMR separates code relevance into at least two rubrics: semantic evidence and dependency support. The paper reports up to 31% additional token savings on multi-turn tasks relative to existing pruners in its evaluated settings.

Syntavra adaptation:

- preserve exact edit anchors first;
- preserve dependency support separately from semantic evidence;
- use AST/graph signals to supervise or verify the structural rubric;
- run only on residual code after symbol/range retrieval;
- no-expansion and fidelity gates remain mandatory.

### U-P1-03 Cross-Lingual Token Arbitrage

Research reference: arXiv:2606.03618.

For multilingual prompts, especially tokenizer-expensive languages, create faithful candidate representations such as:

`original -> compact original-language -> compact English -> structured task form -> SWIR`

Tokenize every candidate under the target provider tokenizer and choose the smallest candidate that passes semantic/constraint preservation. The cited work reports 34-47% prompt-token reduction on its multilingual coding benchmark and up to 18.8% total-token reduction, but those numbers remain external evidence until reproduced in Syntavra.

Hard rules:

- user constraints, literals, paths, code, identifiers and quoted exact text remain exact;
- translation/rewrite must never silently broaden or narrow the task;
- no-expansion fallback to original;
- target-provider tokenizer measurement decides, not character count.

### U-P1-04 Context Value Predictor

Predict the marginal decision/verifier value of an optional context item before provider admission.

Signals may include relevance, dependency centrality, novelty, freshness, trust, prior recall frequency, action sensitivity and exact-required status.

Promotion begins in shadow mode. Mandatory/security/user/exact/verifier evidence can never be removed merely because a predictor scores it low.

### U-P1-05 Step-Level Reasoning Budgeter

Budget reasoning per decision step rather than only per request.

Examples:

- deterministic selection -> minimal/native-low reasoning;
- ambiguous architecture decision -> higher reasoning lease;
- verifier failure diagnosis -> temporarily raise budget;
- post-verification rendering -> no hidden reasoning budget beyond deterministic formatting where possible.

Provider-native reasoning controls are preferred. Learned/open-model reasoning pruning remains experimental.

### U-P1-06 Provider / Tokenizer Arbitrage Router

For allowed providers/models, choose the route by **expected verified cost**, not nominal token count alone.

Inputs include:

- target tokenizer cost for the prepared representation;
- provider pricing/cache behavior;
- expected reasoning/output budget;
- latency;
- tool capability;
- historical verifier success for the task family;
- privacy/security/policy constraints.

A cheaper tokenizer is not selected if it materially lowers verified success.

### U-P1-07 Learned Tool Result Visibility Policy

Learn how much of each tool-result family should stay warm based on actual recall behavior.

Policy can tune:

- preview length;
- active-result lease duration;
- failure-vs-success visibility;
- delta baseline retention;
- recall range defaults.

Exact recovery and failure/security pinning remain hard constraints.

## 5. Execution waves

```text
TE-U0  ultra-low baseline + owner reconciliation
TE-U1  tool-chain fusion + intermediate-result elimination
TE-U2  action-preserving residual observations
TE-U3  multi-rubric residual code pruning
TE-U4  cross-lingual / representation / tokenizer arbitrage
TE-U5  context-value prediction + step-level reasoning budgets
TE-U6  zero-inference answer synthesis + verified micro-agent macros
TE-U7  compound long-trajectory A/B + provider-receipt promotion
```

## 6. Compound optimization order

Do not add component percentages arithmetically. These mechanisms overlap.

Preferred flow:

`candidate state`
`-> deterministic exclusion/supersession`
`-> exact artifact folding`
`-> tool-chain fusion/query pushdown`
`-> graph/symbol/range retrieval`
`-> optional action-preserving observation compression`
`-> optional multi-rubric code pruning`
`-> representation/tokenizer arbitrage`
`-> SWIR/provider envelope`
`-> step-level reasoning budget`
`-> minimal output contract`
`-> verifier`
`-> macro/cache learning`

The main KPI remains `Verified Provider Cost / Successful Task`; the frontier KPI is `Provider Tokens / New Verified Information`.

## 7. Promotion and rollback gates

Every ultra-low mechanism requires:

1. frozen baseline/candidate task equivalence;
2. provider-observed token receipts where a provider-saving claim is made;
3. verifier/security non-inferiority;
4. exact recovery for evicted evidence or an explicit fidelity contract;
5. extra recovery/tool turns included in total cost;
6. latency/cost accounting, not token count in isolation;
7. no-expansion under the target tokenizer;
8. fail-closed fallback on uncertainty;
9. workload-specific promotion rather than universal enablement;
10. automatic rollback on quality or cost-per-success regression.

## 8. Claim boundary

CoACT, LaMR, Anthropic programmatic tool calling and cross-lingual token-arbitrage results are external evidence. They motivate Syntavra mechanisms but do not prove current Syntavra end-to-end savings.

The aggressive 0-500 / 500-2K / 2K-5K bands are research/engineering stretch targets until paired provider receipts and verifier-equivalent workloads certify them.
