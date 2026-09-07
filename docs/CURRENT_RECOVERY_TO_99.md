# Syntavra Recovery-to-9.9 Current Authority

Status: **ACTIVE / CODE-FIRST / ROADMAP-FROZEN / CLAIM-FAIL-CLOSED**  
Date: 2026-09-07  
Machine contract: `contracts/python/recovery-to-99-v1.json`

This is the current execution entrypoint for recovery work. Historical HyperEfficiency, Token Economy and Ultra-Low Frontier documents remain lineage and research evidence. They do not outrank this file while the recovery gate is active.

## 1. Non-negotiable scoring rule

A target score is not a score. `9.9+` is earned only when every required gate for an area is closed by code, tests and the correct class of evidence.

Evidence levels:

- **E0 IDEA**: prose only.
- **E1 IMPLEMENTED**: runtime code exists.
- **E2 UNIT-PROVEN**: deterministic tests pass.
- **E3 INTEGRATED**: product execution path uses it.
- **E4 END-TO-END-PROVEN**: frozen workload passes through the product path.
- **E5 PROVIDER-PROVEN**: paired provider-observed receipts exist under identical workload/model/provider/verifier/effort conditions.
- **E6 RELEASE-PROVEN**: exact-head required CI, rollback, dogfood and release gates are green.

No area receives `9.9` with an open required gate. Provider proof cannot exceed `5.0` without E5 evidence. Readiness cannot exceed `8.0` without E5 plus required exact-head CI.

## 2. Canonical owner model

All active work maps to one of exactly fifteen product owners:

`context, retrieval, evidence, memory, tools, execution, verification, provider_gateway, inference_reuse, model_routing, output, measurement, security, local_runtime, experience_distillation`

A new idea is a policy, strategy, adapter, experiment or subcomponent of one owner unless reconciliation proves that no owner can contain it. New persistent stores, public routers and CAP namespaces are forbidden during recovery.

## 3. Recovery waves

### R99-0: Stop lying to ourselves

Deliverables:

- freeze new frontier versions;
- make this file + machine contract the current recovery authority;
- separate local estimates, target-tokenizer counts, provider-observed tokens and billed cost;
- require code/test/proof references for every promoted status;
- count retries, failed attempts, recovery, reacquisition and cache rewrite cost.

Exit: no public or internal score can be inflated by an estimate masquerading as evidence.

### R99-1: Constant-context runtime

Required product behavior:

- initial repository packet is bounded and source bodies are not auto-opened from semantic hits;
- every model turn is recompiled from task + active evidence + bounded causal receipts;
- historical raw tool bodies are never replayed merely because they occurred earlier;
- unchanged results collapse to typed receipts;
- newer exact observations supersede older observations;
- exact raw evidence remains recoverable before any payload is evicted;
- current provider-token envelope is rebound to the newly compiled packet every turn;
- user instructions, security evidence and verifier failures never silently truncate.

Exit: 50+ tool rounds remain bounded by active evidence, not transcript length.

### R99-2: Retrieval and tool-call elimination

Implement in this order:

1. query pushdown with strict field projection, bounded top-k and local predicates;
2. symbol/range retrieval as default, whole-file reads as bounded compatibility fallback;
3. fused deterministic `search -> select -> ranged read` execution that exposes only the final compact observation to the model;
4. no-change/delta responses for polling/repeated reads;
5. negative-knowledge cache for verified no-result/no-change observations with dependency invalidation;
6. counterfactual tool suppression when a local deterministic check proves a call cannot alter the decision state.

Exit: routine retrieval no longer spends one provider turn per deterministic transformation.

### R99-3: Verified inference elimination

Build an exact inference-skip lane around the existing verifier, evidence and execution owners.

Cache identity must include at minimum:

- project scope;
- exact repository/worktree state fingerprint;
- normalized task/instruction identity;
- verifier contract;
- security/policy fingerprint;
- relevant tool/schema/runtime versions.

Only a previously **fully verified** patch/result may enter the cache. An exact hit may skip provider inference, but the replayed artifact still passes through patch application and the verifier. Any fingerprint mismatch, apply failure, verifier failure or uncertainty invalidates the hit and falls back to normal inference.

Exit: B0/B8 exact repeats can legitimately record `provider_calls=0` without weakening verification.

### R99-4: Repair-loop economics

The loop tracks:

- failure signature;
- patch/diff signature;
- newly acquired information;
- verifier delta;
- repository state delta;
- repeated tool/result identity;
- expected next-attempt value versus token cost.

Repeated patch/failure loops are already forbidden; recovery hardening adds semantic early-stop and strategy-change rules. A provider judge is not called merely to decide whether another provider call is worthwhile.

Exit: no long-tail retry storm can consume unbounded provider tokens without new evidence or explicit necessity.

### R99-5: Security/property closure

Required suites cover:

- random exact externalization round trips;
- stale/missing/corrupt handles;
- cross-scope evidence reuse;
- delta baseline invalidation;
- prompt/tool-output injection isolation;
- SWIR no-expansion and exact-recovery rules;
- mandatory instruction preservation;
- cache fingerprint mutation;
- inference-skip stale patch rejection;
- verifier-failure evidence retention.

Default uncertainty behavior is exact/natural/fallback, never silent lossy success.

Exit: zero known false verified cache hits and zero silent mandatory evidence loss in the frozen adversarial corpus.

### R99-6: Frozen benchmark and accounting

Authority corpus: `contracts/python/token-economy-frozen-workloads-v1.json`.

Every B0-B9 workload reports component, end-to-end and long-session measurements separately. Cold and warm variants remain separate. The principal KPI is:

`Verified Provider Cost / Successful Task`

Secondary KPIs:

`Provider Tokens / Successful Task`, `Provider Tokens / New Verified Information`, `Provider Calls / Task`, `Inference-Free Task Fraction`, `Reacquisition Cost`, `Retry Waste`, cache reads/writes, tool/result tokens, reasoning tokens and output tokens.

Local structural/tokenizer benchmarks are regression evidence only. They never become provider-billed savings claims.

Exit: frozen baseline/candidate runner can reproduce task identity and verifier semantics before a provider is attached.

### R99-7: Provider proof

For every claimed workload family:

- same frozen task/repository/verifier;
- same provider, model and reasoning/effort settings within a pair;
- at least three repetitions per arm;
- provider request/response identity when exposed;
- fresh input, cached input, reasoning and output usage when exposed;
- failed attempts included in cost;
- baseline and candidate receipts retained and linked to the claim;
- no arithmetic addition of overlapping component savings.

Provider proof is intentionally impossible to fabricate offline. Missing provider receipts keep the gate open.

Exit: public savings claims resolve to paired receipt IDs.

### R99-8: Release proof

One exact-head recovery workflow must gate the critical surfaces:

- constant-context/runtime tests;
- security/property tests;
- local token-economy regression benchmark;
- provider envelope and observation contracts;
- MCP/tool-schema stability;
- public surface/dual-engine consistency;
- recovery contract certification;
- clean repository tree;
- exact-head artifact upload.

After that, dogfood runs must show no unexplained envelope overflow, rollback must be proven, and no P0/P1 correctness blocker may remain.

Exit: only then is `readiness >= 9.9` a defensible statement.

## 4. Area-specific 9.9 definition

| Area | 9.9 requires |
|---|---|
| Architecture | one canonical owner per active feature, zero duplicate persistent stores/public routers, explicit dependency/invalidation edges, roadmap rows reconciled without parallel namespace |
| Token economics | >=95% provider-visible mass attributable, all retry/recovery/reacquisition counted, non-inferior quality, lower net verified provider cost on promoted workloads |
| Security | exact recovery for all evicted evidence, property/fuzz coverage, stale/cross-scope reuse fails closed, mandatory evidence never silently lost |
| Runtime | constant context, bounded retrieval, externalization, envelope binding, query pushdown, fusion and verified inference skip in the product path |
| Provider proof | paired provider-observed receipts, identical pair conditions, >=3 repetitions, receipt-linked claims |
| Benchmark | frozen B0-B9, cold/warm separation, adversarial safety, end-to-end and long-horizon accounting, verified-cost Pareto reporting |
| CI | all relevant required checks green on exact head, recovery gate required, exact-head artifacts, no duplicate manual dispatch |
| Roadmap | this authority is current, frontier versions frozen, historical plans are lineage only, active work has owner/status/code/test/proof/blocker |
| Roadmap/code | recovery remains code-first; no implemented/provider-proven status without code/receipt references |
| Product potential | narrow MVP wins real coding tasks end-to-end without relying on research-only features |
| Readiness | all above gates plus dogfood, rollback, release and zero known P0/P1 correctness blocker |

## 5. Current hard blockers

The following cannot be wished away by documentation:

- paired real provider-observed A/B receipts have not yet been collected for the frozen corpus;
- the coding-agent loop still needs full query-pushdown/fused retrieval and verified inference-skip integration;
- the complete cache/delta/SWIR/invalidation property matrix is not yet closed;
- exact-head recovery CI must turn green after the recovery commits;
- B0-B9 provider replay/dogfood must be executed before provider proof/readiness can reach 9.9.

These blockers are deliberately visible. Hiding them would improve the scorecard and degrade the product, a trade humans somehow keep rediscovering.
