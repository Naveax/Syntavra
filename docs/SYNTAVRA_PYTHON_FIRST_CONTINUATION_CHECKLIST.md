# Syntavra Python-First Continuation Checklist

Status checkpoint: **2026-09-07**

This is the current operational continuation checklist. Historical checklists remain in Git history. The admitted post-280 HyperEfficiency track extends, but does not reopen, the frozen Python-first closure.

## Hard rules

- [x] `PYTHON_COMPLETE(v1)=true`.
- [x] Python 236-270 and 276-280 remain closed/certified under existing authorities.
- [x] 271-275 remain deferred Rust-transition work.
- [x] `rust_resume_allowed=false`; `rust_retired=true`.
- [x] Rust production promotion remains 174/245 with 71 remaining.
- [x] CAP-0281..CAP-1564 are admitted as `ADMITTED_RECONCILIATION`, not declared missing implementation.
- [x] New candidates must first be classified `EXISTS/HARDEN/UNIFY/NEW/CERTIFY/EXTERNAL/DEFERRED`.
- [x] Reuse canonical stores, routers, registries, indexes, ledgers and public surfaces; default public-surface growth is zero.
- [x] Provider-billed superiority/adoption/maturity remain external-evidence gated.
- [x] For the same SHA/workflow/input, never create duplicate active Actions. Track the existing run ID and continue independent work.

## Current authorities

1. `docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md`
2. `docs/UNIFIED_PLAN.md`
3. `docs/plans/HYPEREFFICIENCY_MASTER_ROADMAP_V6.md`
4. `contracts/python/hyperefficiency-roadmap-v1.json`
5. `contracts/python/capability-completeness-registry-v1.json` and `-v2.json` for the frozen <=280 authority
6. `contracts/python/python-authority-v1.json` and `rust-feature-freeze-guard-v1.json`

## Immediate exact task: M6-0 reconciliation

Do not implement by capability number. Reconcile the 27 fixed P0 families first, in the order recorded by the live checkpoint and master roadmap. For every family record:

- canonical current owner(s);
- classification;
- already-existing evidence/tests;
- missing behavior only if evidence proves it;
- dependency/invalidation edges;
- verifier/acceptance contract;
- minimal implementation scope if `NEW/HARDEN/UNIFY`;
- external evidence boundary if `CERTIFY/EXTERNAL`.

## Promotion discipline

A capability may move beyond reconciliation only through:

```text
requirement/spec
→ canonical owner
→ minimal mutation scope
→ verifier plan
→ implementation
→ targeted verification
→ regression/security verification
→ cost/latency receipt where relevant
→ exact recovery/provenance
→ state update
```

Frontier output is a candidate until independently verified. Cheap/local verified results do not escalate merely because a larger model exists. Repeated successful frontier work must be harvested into reusable strategy/abstraction when safe.
