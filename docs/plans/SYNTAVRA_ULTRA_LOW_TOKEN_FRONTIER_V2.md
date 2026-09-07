# Syntavra Ultra-Low Token Frontier V2

Status: RESEARCH-ADMITTED / EXECUTION-OVERLAY / EXTENDS V1 / NO NEW CAP NAMESPACE  
Date: 2026-09-07  
Authority: HyperEfficiency Master Roadmap V6 + Token Economy Backlog V1/V2 + Ultra-Low Frontier V1

## 1. Objective

V2 continues the ultra-low program below the V1 stretch bands. The goal is not arbitrary prompt minimization. The goal is to remove provider-visible work while preserving task success, exact constraints, security and verifier outcomes.

Frontier order:

`eliminate -> fuse -> retrieve only on uncertainty -> operate on structure -> preserve action -> constrain generation -> stop when information gain is exhausted -> verify`

No new CAP namespace is created. All mechanisms must reconcile to existing owners before implementation.

## 2. Research stretch bands

These are research/promotion targets, not current measured product claims.

| Workload | V1 stretch | V2 research stretch |
|---|---:|---:|
| verified repeated deterministic | 0 | 0 |
| easy / warm / highly reusable | 0-500 | 0-250 |
| easy but frontier reasoning required | 500-2,000 | 250-1,000 |
| normal scoped coding-agent task | 2,000-5,000 | 1,000-3,000 |
| hard but well-scoped coding task | 4,000-8,000 | 2,500-6,000 before necessity-backed overflow |

Research reduction gates:

- easy/warm avoidable-token target >=99%, stretch >=99.75% where the workload is eligible;
- normal scoped avoidable-token target >=97%, stretch >=99% where irreducible evidence permits;
- zero provider tokens remains the correct target for complete verifier-gated deterministic reuse;
- recovery, retries, repair calls and tool-search calls count in the end-to-end total;
- quality/verifier/security must remain non-inferior to the frozen baseline.

## 3. New mechanisms

### U2-P0-01 AST-Native Action Space

Classification: `UNIFY/HARDEN`  
Owners: repository intelligence + edit compiler + SWIR/programmatic execution

Prefer symbol/AST operations over regenerating unchanged code or verbose textual diffs when the language/parser/edit operation permits it.

Candidate actions include:

- replace symbol body;
- insert/delete/move declaration;
- modify parameter/attribute/import;
- bounded structural splice;
- exact literal/range fallback.

Promotion gates:

- AST identity is tied to exact file/worktree hash;
- edits are deterministic and round-trip verified;
- formatting/semantic verifier runs after application;
- unsupported/ambiguous syntax falls back to text/range edit;
- model output must not repeat unchanged source merely to describe an edit.

### U2-P0-02 Adaptive Edit Format Router

Classification: `HARDEN`  
Owners: edit compiler + TokenPerSuccessOptimizer

For each edit, compare allowed representations such as deterministic structural edit, AST action, block/function diff, JSON/structured patch, unified diff and full rewrite.

Route by expected verified cost:

`representation_tokens + failure_probability * retry_cost + verifier_cost`

Full-file rewrite is last resort for edits that can be represented more cheaply and safely.

### U2-P0-03 Adaptive Retrieval Budget Allocator

Classification: `HARDEN`  
Owners: ContextValuePredictor + repository retrieval + ContextAdmissionController

Retrieval depth is a decision, not a fixed top-k.

Suggested lanes:

- confidence sufficient -> retrieve 0;
- localized uncertainty -> one symbol/range;
- dependency uncertainty -> bounded graph expansion;
- unresolved failure -> targeted failure/dependency evidence;
- broad uncertainty -> wider retrieval under explicit budget.

Promotion requires recall of mandatory evidence and lower provider tokens per successful task than fixed-k baselines.

### U2-P0-04 Semantic Early-Stop Governor

Classification: `NEW/HARDEN`  
Owners: agent loop + SemanticStateMachine + VerifierGraph

Stop exploration/reasoning/tool loops when additional rounds are no longer changing the decision or verification state.

Signals:

- marginal new verified information;
- state/diff change;
- verifier delta;
- unresolved uncertainty;
- repeated action/failure signature;
- expected value of another round.

No early stop is allowed while required verification or unresolved user constraints remain.

### U2-P0-05 Grammar-Constrained SWIR / Structured Output

Classification: `HARDEN/PROVIDER_GATED`  
Owners: SWIR + ProviderGateway + OutputGovernor

Where provider/self-hosted decoding supports schemas/grammars, constrain model output to the selected compact protocol rather than merely asking the model to follow it.

Goals:

- prohibit filler outside the answer contract;
- reduce parser-repair prompts;
- reduce malformed tool/patch retries;
- keep compact operators byte/token stable;
- use deterministic local rendering after validation.

A valid schema is not proof of semantic correctness; verifier gates remain mandatory.

### U2-P0-06 Retry Economics / Repair-Loop Governor

Classification: `NEW/HARDEN`  
Owners: agent runtime + failure ontology + TokenPerSuccessOptimizer + VerifierGraph

Before repeating a failed action, estimate whether the next attempt is likely to provide enough new value to justify its token/tool cost.

Rules:

- identical failure signature + unchanged causal state cannot trigger blind retry loops;
- repeated verifier failures require strategy/state change before another equivalent attempt;
- retry budget is task/risk aware;
- escalation must preserve exact failure evidence while collapsing repeated logs;
- stop/route/repair decisions are recorded for counterfactual evaluation.

### U2-P1-01 Subagent Shared-Context Object Graph

Classification: `HARDEN`  
Owners: subagent context firewall + TypedContextObjectStore + evidence/artifact handles

Subagents receive stable shared immutable context objects by handle instead of duplicating the same system/tool/repository payload in every child context. Each subagent gets only its local delta plus required shared handles.

Savings are counted only when the provider actually avoids repeated tokens; local handle storage by itself is not a provider-token claim.

### U2-P1-02 Best-Round / Best-State Selection

Classification: `HARDEN`  
Owners: SemanticStateMachine + VerifierGraph + agent runtime

Do not assume the latest reasoning/tool round is the best state. Preserve compact candidate-state receipts and select the highest verified state, allowing later unproductive exploration to be discarded without replay.

### U2-P1-03 Counterfactual Context Drop Testing

Classification: `NEW/EXPERIMENTAL`  
Owners: ContextValuePredictor + offline policy evaluation

Offline, remove candidate context objects and replay frozen decisions/verifiers to estimate whether they actually changed the successful trajectory. Use this to train admission/drop policies, never as online authority before shadow validation.

## 4. Execution waves

```text
TE-U8   AST-native action space + edit-format routing
TE-U9   adaptive retrieval budgets
TE-U10  semantic early-stop + retry economics
TE-U11  grammar-constrained SWIR / structured compact outputs
TE-U12  subagent shared-context deduplication
TE-U13  best-state selection + counterfactual drop learning
TE-U14  compound 0-250 / 250-1K / 1-3K certification
```

V1 waves TE-U0..TE-U7 remain active prerequisites. V2 waves do not replace deterministic ingestion/folding, constant-context history, query pushdown, inference skipping or macro execution.

## 5. Measurement additions

Track at minimum:

- unchanged source tokens avoided by structural edits;
- edit representation selected and candidate token counts;
- edit retry/failure rate by representation;
- retrieval budget requested/used and mandatory-evidence recall;
- zero-retrieval successful turns;
- early-stop rounds avoided;
- repeated failure signatures prevented;
- malformed structured-output retries prevented;
- subagent shared-prefix/context tokens avoided where provider-observable;
- best-state recovery count;
- counterfactual context-drop impact;
- end-to-end fresh/cached input, output, reasoning, tool/schema and recovery tokens.

## 6. Hard invariants

1. Smaller edit format cannot weaken edit/verifier correctness.
2. Retrieval may be zero only when admission policy can justify that no additional evidence is required.
3. Early stop cannot skip mandatory verification.
4. Grammar/schema compliance never substitutes for semantic verification.
5. Repeated retries require new causal evidence or strategy change.
6. Shared subagent handles must preserve isolation/security boundaries.
7. Counterfactual learned policies remain shadow/offline until promoted.
8. All representation choices obey target-tokenizer no-expansion.
9. Provider savings claims require paired provider-observed receipts.
10. V2 bands are research targets until certified under frozen equivalent workloads.

## 7. Long-term frontier

The asymptotic target is not universal zero tokens. Some tasks contain irreducible user requirements, exact evidence and genuinely novel reasoning. Syntavra should therefore drive each task toward:

`minimum verified provider work = irreducible information + irreducible uncertainty`

Everything else is optimization debt.