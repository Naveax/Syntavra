# Syntavra Recovery-to-9.9 Current Authority

Status: **ACTIVE / CODE-FIRST / ROADMAP-FROZEN / CLAIM-FAIL-CLOSED**  
Date: 2026-09-08  
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

Provider-call avoidance is counted only when the call is prevented **before inference** by an exact local proof, verified replay, or equivalent provider-observed receipt. Rejecting a repeated answer after generation is not a token saving. An executable corpus or provider-replay plan is E2/E4 infrastructure, not E5 provider proof.

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

Current integrated behavior:

1. query pushdown uses strict field projection, bounded top-k and local predicates;
2. provider-controlled projection/filter material is allow-listed **before graph access**, including empty-result cases;
3. ranged/symbol-scoped inspection is the preferred source path;
4. deterministic `search -> select -> ranged read` may fuse into one local observation when identity is unique;
5. ambiguous identities fail closed to candidate disclosure rather than guessing a source;
6. intermediate graph rows stay local on a successful fused read;
7. no-change/supersession receipts prevent raw observation recurrence.

Still open in this wave: dependency-invalidated negative-knowledge caching and broader counterfactual tool suppression. These are lower-floor hardening opportunities; they are not allowed to replace correctness or evidence gates.

Exit: routine retrieval no longer spends one provider turn per deterministic transformation and unknown retrieval policy never bypasses validation because the result set happened to be empty.

### R99-3: Verified inference elimination

An exact inference-skip lane exists around the existing verifier, evidence and execution owners.

Cache identity includes repository state plus task/verifier/policy material. Only fully verified results may be replayed. Exact hits may skip provider inference, but replayed patches still pass patch application and the verifier. Fingerprint mismatch, apply failure, verifier failure or uncertainty invalidates/falls back rather than fabricating success.

Exit: eligible exact repeats can legitimately record `provider_calls=0` without weakening verification.

### R99-4: Repair-loop economics

The repair loop treats retry suppression as an **information-equivalence proof**, not as a textual-failure heuristic.

Required behavior:

- fingerprint the exact mutable workspace before a repair provider call;
- pair that fingerprint with the normalized verifier/apply failure fingerprint;
- allow one repair presentation for a new exact `{failure, workspace-state}` pair;
- if the same exact pair reappears, stop **before** another provider call and record `provider_calls_avoided`;
- the same textual failure on a different exact workspace state remains eligible for repair;
- if exact workspace equivalence cannot be proven, allow the provider call rather than risk a false stop;
- keep repeated-patch protection independently, because identical patch recurrence and identical failure/state recurrence are different pathologies;
- never call another provider merely to judge whether a provider retry is worthwhile.

This replaces failure-only anti-loop logic, which could both waste inference on unchanged state and incorrectly stop a progressing repair that produced the same verifier message on a new worktree state.

Exit: no exact unchanged failure/state pair is presented to the provider repeatedly, while state-changing repair trajectories remain available.

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
- retrieval projection/filter fail-closed behavior before graph access;
- retry equivalence uncertainty preserving solve quality;
- verifier-failure evidence retention.

Default uncertainty behavior is exact/natural/fallback, never silent lossy success.

Exit: zero known false verified cache hits and zero silent mandatory evidence loss in the frozen adversarial corpus.

### R99-6: Frozen benchmark and accounting

Authority corpus: `contracts/python/token-economy-frozen-workloads-v1.json`.

The B0-B9 corpus is now executable and portable through `benchmarks/token_economy_frozen_corpus.py`. Fixture bytes, inline verifier, fixed Git metadata, repository commit/tree and portable workload identity are deterministic. Local repository path is a transport locator and is excluded from the portable workload identity. Two independent materialization roots must produce identical workload identity, tree and commit. Every initial fixture must fail its verifier, preventing an already-solved benchmark from earning success by existing peacefully.

Every B0-B9 workload reports component, end-to-end and long-session measurements separately. Cold and warm variants remain separate and are selected exactly from the workload contract. The principal KPI is:

`Verified Provider Cost / Successful Task`

Secondary KPIs:

`Provider Tokens / Successful Task`, `Provider Tokens / New Verified Information`, `Provider Calls / Task`, `Inference-Free Task Fraction`, `Reacquisition Cost`, `Retry Waste`, cache reads/writes, tool/result tokens, reasoning tokens and output tokens.

Local structural/tokenizer benchmarks are regression evidence only. They never become provider-billed savings claims.

Exit: frozen baseline/candidate runner can reproduce task identity and verifier semantics before a provider is attached.

### R99-7: Provider proof

The deterministic provider replay orchestrator is `benchmarks/token_economy_provider_replay.py`. It reuses the existing SignalBench receipt/comparison authority rather than creating another proof store.

Replay policy:

- exactly two arms, baseline and candidate;
- same model, reasoning/effort and context window before execution;
- workload-specific cold/warm variants come from the frozen B0-B9 contract instead of a Cartesian cache-mode expansion;
- at least three repetitions per arm/workload variant;
- explicit credential-like values are forbidden in the arms document; credential material must be inherited at execution time;
- same frozen task, repository commit/tree, prompt, verifier, permissions, model/effort/context and hardware identity within each comparison pair;
- provider-observed receipt required for both sides of a proof pair;
- provider identity must match within each pair;
- fresh input, cached input, reasoning and output usage are recorded when exposed;
- failed attempts remain in total cost;
- retries prevented by inference-skip/retry-economics are recorded separately from prompt compression;
- baseline and candidate receipts remain linked to the claim;
- overlapping component percentages are never arithmetically added.

Plan mode is deliberately offline and reports `REPLAY_PLAN_ONLY_NOT_PROVIDER_PROOF`. Execute mode remains fail-closed unless pair-identity issues are zero and the existing comparison authority permits the claim. The planner being present does **not** move provider proof above E4.

Provider proof is intentionally impossible to fabricate offline. Missing provider receipts keep the gate open.

Exit: public savings claims resolve to paired receipt IDs.

### R99-8: Release proof

One exact-head recovery workflow must gate the critical active surfaces:

- constant-context/runtime tests;
- retrieval fail-closed tests;
- exact-state retry-economics tests;
- executable frozen-corpus and provider-replay planning regressions;
- security/property tests;
- local token-economy regression benchmark;
- provider envelope and observation contracts;
- MCP/tool-schema stability;
- public surface/dual-engine consistency;
- recovery contract certification;
- clean repository tree;
- exact-head artifact upload.

After that, dogfood runs must show no unexplained envelope overflow, rollback must be proven, and no P0/P1 correctness blocker may remain.

Rust is currently explicitly retired/frozen: `rust_resume_allowed=false`, `rust_retired=true`, production promotion remains `174/245`, and Remaining-71 parity work requires separate explicit reactivation authority. Remaining-71 differential mismatches are preserved as drift evidence while Rust is retired; they do not authorize hidden Rust feature development and do not by themselves redefine an active Python/product correctness gate. If Rust is reactivated, the corresponding parity checks become blocking again under the reactivation authority.

Exit: only then is `readiness >= 9.9` a defensible statement.

## 4. Area-specific 9.9 definition

| Area | 9.9 requires |
|---|---|
| Architecture | one canonical owner per active feature, zero duplicate persistent stores/public routers, explicit dependency/invalidation edges, roadmap rows reconciled without parallel namespace |
| Token economics | >=95% provider-visible mass attributable, all retry/recovery/reacquisition counted, non-inferior quality, lower net verified provider cost on promoted workloads, pre-inference avoidance separated from post-generation rejection |
| Security | exact recovery for all evicted evidence, property/fuzz coverage, stale/cross-scope reuse fails closed, retrieval policy validates before data access, mandatory evidence never silently lost |
| Runtime | constant context, bounded retrieval, externalization, envelope binding, query pushdown, fusion, verified inference skip and exact-state retry economics in the product path |
| Provider proof | paired provider-observed receipts, identical pair conditions, >=3 repetitions, zero pair-identity issues, receipt-linked claims; replay planning alone earns no E5 credit |
| Benchmark | executable portable B0-B9, exact per-workload cold/warm schedule, adversarial safety, end-to-end and long-horizon accounting, verified-cost Pareto reporting |
| CI | all relevant active required checks green on exact head, recovery gate required, exact-head artifacts, no duplicate manual dispatch; retired-engine evidence remains visible without silently reactivating that engine |
| Roadmap | this authority is current, frontier versions frozen, historical plans are lineage only, active work has owner/status/code/test/proof/blocker |
| Roadmap/code | recovery remains code-first; no implemented/provider-proven status without code/receipt references |
| Product potential | narrow MVP wins real coding tasks end-to-end without relying on research-only features |
| Readiness | all above active gates plus dogfood, rollback, release and zero known P0/P1 correctness blocker |

## 5. Current hard blockers

Closed since the first recovery authority revision:

- query pushdown is integrated into the coding-agent provider path;
- deterministic search/select/ranged-read fusion is integrated;
- verified inference skip is integrated and re-verifies replayed artifacts;
- retrieval projection/filter validation is fail-closed before graph access;
- exact failure/workspace retry economics is integrated before provider inference;
- B0-B9 is an executable portable frozen corpus rather than a prose-only workload list;
- a deterministic contract-driven provider replay planner exists and is CI-tested offline.

Still open and therefore score-capping:

- paired real provider-observed A/B receipts have not yet been collected for the frozen corpus;
- B0-B9 real-provider replay execution/dogfood has not yet closed;
- the full cache/delta/SWIR/handle/invalidation property and fuzz matrix is not yet closed;
- latest exact-head active aggregate CI and release chain must be green after recovery commits;
- a release candidate with zero known P0/P1 correctness blockers is not yet proven.

Parameterized verified macro compilation remains useful post-9.9 token-floor hardening, but it is not a required 9.9 release gate. Treating every research idea as a release blocker would turn the roadmap into a landfill with checkboxes.

These blockers are deliberately visible. Hiding them would improve the scorecard and degrade the product, a trade humans somehow keep rediscovering.
