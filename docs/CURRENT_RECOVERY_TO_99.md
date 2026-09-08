# Syntavra Recovery-to-10 Current Authority

Status: **ACTIVE / CODE-FIRST / ROADMAP-FROZEN / CLAIM-FAIL-CLOSED**  
Date: 2026-09-08  
Machine contract: `contracts/python/recovery-to-99-v1.json`

This is the current execution entrypoint for recovery work. Historical HyperEfficiency, Token Economy and Ultra-Low Frontier documents remain lineage and research evidence. They do not outrank this file while the recovery gate is active.

## 1. Non-negotiable scoring rule

A target score is not a score. `10.0` is earned only when every required gate for an area is closed by code, tests and the correct class of evidence.

Evidence levels:

- **E0 IDEA**: prose only.
- **E1 IMPLEMENTED**: runtime code exists.
- **E2 UNIT-PROVEN**: deterministic tests pass.
- **E3 INTEGRATED**: product execution path uses it.
- **E4 END-TO-END-PROVEN**: frozen workload passes through the product path.
- **E5 PROVIDER-PROVEN**: paired provider-observed receipts exist under identical workload/model/provider/verifier/effort conditions.
- **E6 RELEASE-PROVEN**: exact-head required CI, rollback, dogfood and release gates are green.

No area receives `10.0` with an open required gate. Provider proof cannot exceed `5.0` without E5 evidence. Readiness cannot exceed `8.0` without E5 plus required exact-head CI. External superiority cannot receive `10.0` from one provider/model scope; the 10.0 gate requires at least two independent provider/model scopes, the same baseline/candidate arm versions, workload-level non-regression and re-certified raw E5 replays.

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

Still open in this wave: dependency-invalidated negative-knowledge caching and broader counterfactual tool suppression. These are lower-floor hardening opportunities; they are not required to fake a 10.0 release score and may not replace correctness or evidence gates.

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
- keep repeated-patch protection independently;
- never call another provider merely to judge whether a provider retry is worthwhile.

Exit: no exact unchanged failure/state pair is presented to the provider repeatedly, while state-changing repair trajectories remain available.

### R99-5: Security/property closure

The recovery and release gates now require:

- random exact externalization round trips;
- stale/missing/corrupt handles;
- encrypted evidence corruption rejection;
- cross-scope evidence reuse isolation;
- prompt/tool-output injection isolation;
- SWIR no-expansion and exact-recovery rules;
- mandatory instruction preservation;
- 128-way inference-skip identity mutation checks;
- stale patch integrity invalidation;
- randomized exact failure/workspace retry-equivalence checks;
- retrieval projection/filter fail-closed behavior before graph access;
- sandbox capability probing instead of PATH/binary-presence inference;
- portable fallback only when strict native isolation is not required;
- strict-native probe failure remaining fail-closed;
- verifier-failure evidence retention.

Default uncertainty behavior is exact/natural/fallback, never silent lossy success.

Exit: zero known false verified cache hits and zero silent mandatory evidence loss in the frozen adversarial corpus, with the seeded property matrix enforced by promotion CI rather than merely stored in the repository.

### R99-6: Frozen benchmark and accounting

Authority corpus: `contracts/python/token-economy-frozen-workloads-v1.json`.

The B0-B9 corpus is executable and portable through `benchmarks/token_economy_frozen_corpus.py`. Fixture bytes, inline verifier, fixed Git metadata, repository commit/tree and portable workload identity are deterministic. Local repository path is a transport locator and is excluded from the portable workload identity. Two independent materialization roots must produce identical workload identity, tree and commit. Every initial fixture must fail its verifier.

Every B0-B9 workload reports component, end-to-end and long-session measurements separately. Cold and warm variants remain separate and are selected exactly from the workload contract. The principal KPI is:

`Verified Provider Cost / Successful Task`

Secondary KPIs:

`Provider Tokens / Successful Task`, `Provider Tokens / New Verified Information`, `Provider Calls / Task`, `Inference-Free Task Fraction`, `Reacquisition Cost`, `Retry Waste`, cache reads/writes, tool/result tokens, reasoning tokens and output tokens.

Local structural/tokenizer benchmarks are regression evidence only. They never become provider-billed savings claims.

Exit: frozen baseline/candidate runner can reproduce task identity and verifier semantics before a provider is attached.

### R99-7: Provider proof

The deterministic provider replay orchestrator is `benchmarks/token_economy_provider_replay.py`. It reuses the existing SignalBench receipt/comparison authority rather than creating another proof store.

E5 policy:

- exactly two arms per scope, baseline and candidate;
- same model, reasoning/effort and context window before execution;
- exact baseline/candidate arm versions are stable and embedded in the E5 scope identity;
- workload-specific cold/warm variants come from the frozen B0-B9 contract;
- at least three repetitions per arm/workload variant;
- explicit credential-like values are forbidden in the arms document;
- same frozen task, repository commit/tree, prompt, verifier, permissions, model/effort/context and hardware identity within each comparison pair;
- provider-observed receipt required for both sides of a proof pair;
- provider identity must match within each pair;
- fresh input, cached input, reasoning and output usage are recorded when exposed;
- failed attempts remain in total cost;
- retries prevented before inference are recorded separately from prompt compression;
- baseline and candidate receipts remain linked to the claim;
- overlapping component percentages are never arithmetically added.

E5 validity and superiority are deliberately separate. A valid E5 experiment may show that Syntavra did not win. Scoped superiority additionally requires the hardened comparator and **no B0-B9 workload pass-rate regression**. A 5x claim requires the lower 95% confidence bound, median successful-pair ratio and failure-inclusive efficiency ratio all to clear 5x.

The 10.0 external-superiority gate is stricter again: `certify_multi_scope_superiority` requires at least two independent provider/model scopes, all supplied scopes must prove superiority and workload non-regression, arm/version bindings must agree, and duplicate scopes do not count. `tools/certify_provider_multi_scope.py` re-certifies the raw replays before aggregation so hand-written certificate JSON is not authority.

Plan mode reports `REPLAY_PLAN_ONLY_NOT_PROVIDER_PROOF`. Provider proof remains impossible to fabricate offline. Missing provider receipts keep the gate open.

Exit: public savings claims resolve to paired receipt IDs, and a 10.0 multi-scope superiority score requires two independent live provider/model scopes rather than a repeated single-scope experiment.

### R99-8: Release proof

The exact-head recovery workflow gates the critical active surfaces, including constant-context/runtime tests, retrieval, retry economics, executable frozen corpus, E5 semantics, seeded security/property closure, sandbox capability semantics, local token regression, provider/public-surface contracts, recovery certification and exact-head artifacts.

The Release Main Merge Gate independently re-proves the recovery-critical runtime/security suites instead of assuming another workflow passed. It also certifies exact Git HEAD/tree integrity, generates the candidate SHA-256 source manifest deterministically, runs repository/release validation, preserves the checked-in manifest only as a legacy snapshot, enforces immutable action pins and requires a clean final worktree.

After that, dogfood runs must show no unexplained envelope overflow, rollback must be proven, and no P0/P1 correctness blocker may remain.

Rust is explicitly retired/frozen: `rust_resume_allowed=false`, `rust_retired=true`, production promotion remains `174/245`, and Remaining-71 parity work requires separate explicit reactivation authority. Remaining-71 differential mismatches remain drift evidence while Rust is retired and do not silently redefine the active Python release gate.

Exit: only then is `readiness = 10.0` a defensible statement.

## 4. Area-specific 10.0 definition

| Area | 10.0 requires |
|---|---|
| Architecture | one canonical owner per active feature, zero duplicate persistent stores/public routers, explicit dependency/invalidation edges, roadmap rows reconciled without parallel namespace |
| Token economics | >=95% provider-visible mass attributable, all retry/recovery/reacquisition counted, non-inferior quality and lower net verified provider cost on promoted workloads |
| Security | exact recovery, seeded property closure, stale/cross-scope fail-closed behavior, mandatory evidence preservation and probed sandbox capabilities |
| Runtime | constant context, bounded retrieval, externalization, envelope binding, query pushdown, fusion, verified inference skip, exact-state retry economics and honest sandbox backend admission |
| Provider proof | complete paired provider-observed B0-B9 E5 receipts, stable arm versions, identical pair conditions, >=3 repetitions, zero pair-identity/receipt issues and receipt-linked claims |
| Benchmark | executable portable B0-B9, exact cold/warm schedule, adversarial safety, workload-level non-regression, long-horizon accounting and verified-cost Pareto reporting |
| CI | all relevant active required checks green on exact head; recovery and release gates independently re-prove critical security/runtime behavior |
| Roadmap | this authority is current, frontier versions frozen, historical plans are lineage only, active work has owner/status/code/test/proof/blocker |
| Roadmap/code | recovery remains code-first; no implemented/provider-proven status without code/receipt references |
| Product potential | narrow MVP wins real coding tasks end-to-end without relying on research-only features |
| Readiness | all above active gates plus live E5, dogfood, rollback, release and zero known P0/P1 correctness blocker |

External superiority is tracked alongside these areas. Scoped superiority is not universal superiority; a score of 10.0 requires at least two independent live provider/model scopes under the multi-scope gate.

## 5. Current hard blockers

Closed in the recovery implementation:

- constant-context runtime and exact evidence recovery are integrated;
- query pushdown and deterministic search/select/ranged-read fusion are integrated;
- verified inference skip is integrated and re-verifies replayed artifacts;
- retrieval projection/filter validation is fail-closed before graph access;
- exact failure/workspace retry economics is integrated before provider inference;
- B0-B9 is an executable portable frozen corpus;
- deterministic contract-driven provider replay and E5 certification exist;
- arm-version binding and workload-level superiority non-regression are enforced;
- a two-independent-provider/model multi-scope 10.0 superiority gate exists;
- native Linux sandbox backends require runtime capability probes rather than binary presence;
- the seeded cache/evidence/retry property matrix is required by Recovery and Release Main;
- exact-head release integrity uses Git HEAD/tree plus a generated manifest receipt rather than trusting a stale checked-in snapshot.

Still open and therefore score-capping:

- paired real provider-observed B0-B9 E5 receipts have not yet been collected;
- two independent live provider/model superiority scopes have not yet been collected;
- latest exact-head active CI and release chain must be green after the newest recovery commits;
- B0-B9 live provider execution/dogfood has not yet closed;
- a release candidate with zero known P0/P1 correctness blockers is not yet proven.

Parameterized verified macro compilation remains useful post-recovery token-floor hardening, but it is not a required 10.0 release gate. Treating every research idea as a release blocker would turn the roadmap into a landfill with checkboxes.

These blockers stay visible because hiding them would improve the scorecard and degrade the product, a surprisingly popular human optimization target.
