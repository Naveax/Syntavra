# Syntavra Ultra-Low Token Frontier V3

Status: RESEARCH-ADMITTED / EXECUTION-OVERLAY / EXTENDS V2 / NO NEW CAP NAMESPACE  
Date: 2026-09-07  
Authority: HyperEfficiency Master Roadmap V6 + Token Economy Backlogs + Ultra-Low Frontiers V1/V2

## 1. Objective

V3 turns token reduction into a continuous floor-search problem rather than a sequence of manually chosen fixed targets.

The governing objective is:

`minimum verified provider work = irreducible information + irreducible uncertainty`

For every eligible workload family, Syntavra should progressively lower retrieval, input, reasoning, output and provider-call budgets until the next reduction would violate a frozen verifier/security/non-inferiority gate. The last passing configuration becomes the measured floor for that workload family.

V3 creates no CAP namespace. Existing canonical owners are reused first.

## 2. Research floor bands

These are aggressive research targets, not current product claims and not universal guarantees.

| Workload | V2 research stretch | V3 floor-search target |
|---|---:|---:|
| verified repeated deterministic | 0 | 0 |
| easy / warm / highly reusable | 0-250 | 0-100 |
| easy but frontier reasoning required | 250-1,000 | 100-500 |
| normal scoped coding-agent task | 1,000-3,000 | 500-2,000 |
| hard but well-scoped coding task | 2,500-6,000 | 2,000-5,000 before necessity-backed overflow |

The floor-search system may discover a higher safe floor. Quality wins over the target number.

## 3. New and hardened mechanisms

### U3-P0-01 Verified Token Floor Search / Budget Annealing

Classification: `NEW/HARDEN`  
Owners: TokenPerSuccessOptimizer + offline policy evaluation + SignalBench

For each frozen task family, search downward across provider-visible budgets rather than assuming one global target.

Candidate dimensions:

- retrieval depth;
- admitted context tokens;
- tool-schema/detail level;
- tool-result preview/recall budget;
- reasoning effort/step budget;
- output contract size;
- edit representation;
- provider/model route;
- retry/repair budget.

Search policy:

1. start from the last certified passing configuration;
2. reduce one or more budgets in shadow/offline replay;
3. run the identical verifier/security contract;
4. count all recovery, retry and fallback tokens;
5. keep Pareto-dominant configurations only;
6. promote the lowest stable non-inferior configuration per workload family;
7. roll back automatically on regression.

This is the mechanism that keeps pushing targets lower without replacing engineering evidence with wishful arithmetic.

### U3-P0-02 Local Draft -> Verifier -> Frontier Residual Correction

Classification: `UNIFY/HARDEN`  
Owners: local specialists + VerifierGraph + ProviderGateway + edit compiler

A small local model or deterministic executor may produce the first candidate. The verifier then converts remaining defects into a minimal residual correction contract for the frontier model.

Target path:

`local candidate -> verifier -> minimal failure/constraint slice -> frontier correction only -> verifier`

Hard gates:

- do not send the entire local draft merely to ask the frontier model to rewrite it;
- project only implicated symbols/hunks/constraints where exact identity is available;
- compare expected cloud input inflation against output/reasoning saved;
- bypass local drafting when the predicted correction burden is larger than direct frontier generation;
- local compute is tracked separately from provider-token savings.

### U3-P0-03 Verifier-Guided Failure Projection

Classification: `HARDEN/UNIFY`  
Owners: FailureOntology + QueryCompiler + Tool Result Query Pushdown + ContextAdmissionController

Convert a large failure surface into the smallest exact evidence slice that can change the next action.

Examples:

- failing assertion + implicated symbol + dependency edge;
- compiler diagnostic + exact declaration/import;
- targeted test failure + changed hunk + relevant fixture;
- type error + callsite + signature.

The full raw failure stays recoverable by handle. Provider visibility is driven by causal relevance, not log length.

### U3-P0-04 Incremental State Handoff / Content-Addressed Delta

Classification: `HARDEN/PROVIDER_GATED`  
Owners: TypedContextObjectStore + Context Replay Breaker + ProviderGateway + prompt-cache/state adapters

When the model/provider already has a stable state prefix or server-side state handle, send only newly changed content-addressed state plus invalidation metadata.

Rules:

- unchanged resources are referenced by stable identity rather than reserialized when provider semantics genuinely permit this;
- provider-side state/cache controls are capability-detected;
- a state handle is not counted as token savings unless provider receipts show reduced fresh/billed processing;
- portable fallback remains standard Syntavra context compilation.

### U3-P1-01 Task-Family Local Distillation / Specialist Escalation

Classification: `HARDEN/UNIFY`  
Owners: repository-specific distillation + local specialists + Inference Exception Architecture

Repeated verified frontier work should train or configure narrow local specialists for recurring task families. The local specialist handles the common path; frontier inference receives only residual uncertainty or novel cases.

Promotion requires:

- frozen task-family benchmark;
- verifier/security non-inferiority;
- explicit uncertainty/escalation threshold;
- no silent transfer of one repository's learned assumptions to incompatible state.

### U3-P1-02 Proof-Carrying Zero-Inference Result

Classification: `HARDEN/UNIFY`  
Owners: Zero-Inference Answer Synthesis + VerifierGraph + EvidenceStore

Strengthen zero-inference reuse so every local/macro answer carries a compact proof receipt describing task fingerprint, execution/macro identity, verifier result, exact evidence handles and invalidation state.

The user-facing answer may be rendered locally only from that proof receipt. No frontier call is required when the proof is complete.

### U3-P1-03 Pareto Workflow Selector

Classification: `HARDEN`  
Owners: TokenPerSuccessOptimizer + frontier router + workflow compiler

Maintain empirical Pareto fronts over `quality / verifier success / provider tokens / provider cost / latency / retries` for each workload family. Select only non-dominated workflows.

A workflow that saves raw tokens but causes enough retries, recalls or failures to increase cost-per-success is rejected.

## 4. Execution waves

```text
TE-U15  verified token-floor search + Pareto budget annealing
TE-U16  local draft + verifier + residual frontier correction
TE-U17  verifier-guided failure projection
TE-U18  incremental state handoff / content-addressed provider delta
TE-U19  task-family local distillation + proof-carrying zero-inference
TE-U20  Pareto workflow selector
TE-U21  compound 0-100 / 100-500 / 500-2K certification
```

TE-U0..TE-U14 remain prerequisites/active work. V3 does not replace deterministic ingestion, constant-context history, AST edit routing, adaptive retrieval, early stop or grammar-constrained output.

## 5. Measurement additions

Track at minimum:

- certified token floor by workload/task family;
- floor-search trials and rollback count;
- Pareto-dominant configuration count;
- local-draft direct-vs-residual route choice;
- cloud input inflation caused by local draft review;
- frontier correction tokens vs direct-generation tokens;
- verifier-projected failure tokens vs raw failure tokens;
- incremental state fresh/cached/billed tokens as reported by provider;
- local-specialist solve/escalation rate;
- proof-carrying zero-inference success and invalidation rate;
- total provider tokens/cost per verified successful task including retries/recalls/fallbacks.

## 6. Hard invariants

1. Token floor is discovered per workload family, never asserted universally.
2. Lower token count cannot weaken frozen verifier/security success.
3. Local drafting is disabled when expected cloud correction cost exceeds direct frontier cost.
4. Failure projection preserves exact recovery and mandatory evidence.
5. Provider state/cache handles count as savings only when provider receipts support the claim.
6. Local specialists escalate on uncertainty and incompatible state.
7. Zero-inference answers require complete proof receipts.
8. Pareto selection optimizes successful-task economics, not raw token count.
9. All recovery/retry/fallback calls count in end-to-end totals.
10. Public savings claims remain blocked until paired provider-observed certification exists.

## 7. Research references

Mechanism inspiration/evidence remains external until reproduced in Syntavra:

- CODESTRUCT, ACL 2026 / arXiv:2604.05407: AST-structured action spaces.
- FastEdit: AST-aware minimal edit output.
- AB-RAG, arXiv:2606.29090: adaptive retrieval budgets.
- Semantic Early-Stopping, arXiv:2606.27009: loop termination by semantic/progress signals.
- CoDE-Stop, arXiv:2604.04930 and ESTAR, arXiv:2602.10004: reasoning early-stop.
- Local-Splitter, arXiv:2604.12301: workload-dependent local routing/drafting and cloud-token tradeoffs.
- Thinking Economically / HAB, ACL Findings 2026: hierarchical step/problem reasoning budgets and Pareto optimization.

## 8. Long-term rule

After any lower band is certified, the optimizer is allowed to search below it again. There is no artificial terminal token target other than zero for fully deterministic verified work.

The stopping condition is empirical:

`stop lowering when the next reduction increases verified failure/security risk or total cost-per-success`.
