# Syntavra Token Economy Execution Backlog V3

Status: ACTIVE CONTINUATION / EXTENDS V2 / NO NEW CAP NAMESPACE  
Date: 2026-09-07

## 1. Authority

V3 extends V2 and preserves every open V1/V2 item.

New overlay: `docs/plans/SYNTAVRA_ULTRA_LOW_TOKEN_FRONTIER_V2.md`.

All items are reconciliation work until mapped to canonical owners and classified `EXISTS | HARDEN | UNIFY | NEW | CERTIFY | EXTERNAL | DEFERRED`.

## 2. Next-order queue

After the current deterministic TE-P0 ingestion/constant-context work, continue in this order:

1. `U2-P0-01` AST-Native Action Space.
2. `U2-P0-02` Adaptive Edit Format Router.
3. `U2-P0-03` Adaptive Retrieval Budget Allocator.
4. `U2-P0-04` Semantic Early-Stop Governor.
5. `U2-P0-06` Retry Economics / Repair-Loop Governor.
6. `U2-P0-05` Grammar-Constrained SWIR / Structured Output.
7. `U2-P1-01` Subagent Shared-Context Object Graph.
8. `U2-P1-02` Best-Round / Best-State Selection.
9. `U2-P1-03` Counterfactual Context Drop Testing.
10. Compound TE-U14 provider-receipt certification.

These do not outrank unfinished pre-model folding, supersession, no-change elision, delta history, query pushdown, inference skip or macro compilation. Deterministic elimination still comes first.

## 3. New TODOs

### U2-P0-01 AST-Native Action Space

- [ ] Reconcile edit compiler / repository intelligence / SWIR owners.
- [ ] Define stable symbol/AST identities tied to exact file hashes.
- [ ] Add structural operations for replace/insert/delete/move/attribute/import edits.
- [ ] Require deterministic apply + parse/format/test verifier.
- [ ] Fall back to exact textual/range edit when syntax or identity is ambiguous.
- [ ] Measure unchanged source/output tokens avoided.

### U2-P0-02 Adaptive Edit Format Router

- [ ] Enumerate allowed edit representations per language/provider.
- [ ] Tokenize candidate representations before inference where possible.
- [ ] Estimate representation failure/retry cost from historical receipts.
- [ ] Route by expected verified cost, not smallest raw edit string.
- [ ] Keep full-file rewrite as fallback, not default.

### U2-P0-03 Adaptive Retrieval Budget Allocator

- [ ] Add 0/symbol/range/graph/broad retrieval budget lanes.
- [ ] Use uncertainty, dependency and verifier state to select lane.
- [ ] Pin mandatory/security/user/exact/failure evidence outside learned budget decisions.
- [ ] Track zero-retrieval successful turns and missed-evidence preventions.
- [ ] Compare with fixed-top-k baselines.

### U2-P0-04 Semantic Early-Stop Governor

- [ ] Compute marginal information/state/verifier gain per round.
- [ ] Detect repeated actions, reads, searches and unchanged verifier states.
- [ ] Stop only when no required verification/constraint remains unresolved.
- [ ] Record prevented rounds and false-stop preventions.
- [ ] Shadow-evaluate thresholds/policies before adaptive promotion.

### U2-P0-05 Grammar-Constrained SWIR / Structured Output

- [ ] Capability-detect provider grammar/schema/structured-output support.
- [ ] Compile selected SWIR/output contract into the strictest supported decoding constraint.
- [ ] Reject filler/out-of-contract fields where protocol permits.
- [ ] Use deterministic local renderer after validation.
- [ ] Measure malformed-output and repair-call reductions.
- [ ] Keep semantic/verifier checks separate from syntax/schema validity.

### U2-P0-06 Retry Economics / Repair-Loop Governor

- [ ] Fingerprint failure signatures and causal state.
- [ ] Refuse equivalent blind retry on identical failure + unchanged causal state.
- [ ] Require strategy/evidence/state change before retrying an equivalent failed action.
- [ ] Budget retries by task risk and expected verified value.
- [ ] Collapse repeated logs while preserving exact failure artifacts.
- [ ] Measure long-tail provider-token reductions and regression risk.

### U2-P1-01 Subagent Shared-Context Object Graph

- [ ] Represent immutable shared instructions/tool schemas/repository evidence as typed shared objects.
- [ ] Give each subagent only required handles + local deltas.
- [ ] Preserve context-firewall isolation and authorization.
- [ ] Measure provider-visible duplicate context avoided only where provider semantics make this real.

### U2-P1-02 Best-Round / Best-State Selection

- [ ] Score candidate states by verifier status, unresolved constraints, confidence and cost.
- [ ] Allow a previous verified state to beat a later unproductive state.
- [ ] Store compact state receipts, not replayed reasoning bodies.
- [ ] Prevent late exploratory degradation from becoming final output.

### U2-P1-03 Counterfactual Context Drop Testing

- [ ] Offline replay frozen tasks with individual context objects removed.
- [ ] Measure decision/verifier/action divergence.
- [ ] Feed results into Context Value Predictor training/evaluation.
- [ ] Keep online policy shadow-only until non-inferiority and rollback gates pass.

## 4. V2 frontier waves

```text
TE-U8   AST-native edit + adaptive edit routing
TE-U9   adaptive retrieval budget
TE-U10  semantic early stop + retry economics
TE-U11  grammar-constrained SWIR/output
TE-U12  subagent shared-context deduplication
TE-U13  best-state + counterfactual context learning
TE-U14  compound ultra-low certification
```

## 5. Research stretch targets

Not current product claims:

```text
verified repeated deterministic: 0
easy/warm eligible:             0-250
easy frontier-needed:           250-1,000
normal scoped coding:           1,000-3,000
hard scoped coding:             2,500-6,000 before necessity overflow
easy avoidable reduction:       >=99% target / >=99.75% stretch
normal scoped avoidable:        >=97% target / >=99% stretch
```

V1/V2 base bands remain promotion authority until these lower bands have frozen-task paired provider receipts and non-inferior verifier/security results.

## 6. Completion rule

V3 is complete only when every promoted U2 item has a canonical owner, dependency/invalidation contract, verifier, target-tokenizer accounting, fallback/rollback path and provider-observed evidence for claimed savings.

The optimization objective remains: **minimum verified provider work, not minimum text at any cost**.