# Syntavra Python-First Live Checkpoint

Updated: **2026-08-18**

This file records the current execution state. The master roadmap and continuation checklist remain append-only planning authorities; this file is the volatile checkpoint that may be replaced by a newer checkpoint as work advances.

## Current release prerequisite

### PR #132 — release authority hardening

- State: OPEN, mergeable, non-draft.
- Branch: `agent/release-authority-action-pins`
- Exact certification head: `da258fdacee972775e5800fa1c6084116041cab1`
- Final tree contains no temporary manifest-refresh workflow.
- `MANIFEST.sha256` was regenerated from the exact final tree.
- Final manifest blob SHA: `8d572679be28c89d05edf89e2edb6e0081199e38`.
- Repository-hygiene attestation parsing now requires immutable 40-character `actions/attest` SHAs.
- Regression coverage rejects mutable `@v4` and short-SHA attestation refs.
- A prior Release Package Provenance run passed after the hygiene repair.
- Final exact-head CI was retriggered on an empty commit with the same final tree because the manifest-finalizer commit was authored by `github-actions[bot]` and GitHub marked its PR-triggered workflows `action_required` without creating jobs.
- Current exact-head release/Python workflows are queued/pending. They are **not yet certified PASS**.
- Do not merge #132 until the final exact-head release/Python gates actually execute and pass.

## Python-first implementation

### PR #134 — `python_authority_v1`

- State: OPEN DRAFT.
- Base: `agent/release-authority-action-pins` (stacked on PR #132).
- Head branch: `agent/python-authority-v1`.
- Current authority head: `9043e38209383e7ab1fb08ff8598d7ea151b8364`.
- Must not merge before PR #132.
- Manifest refresh for this PR is deferred until the release-hardening base is finalized/merged.

Implemented in the authority milestone:

- `contracts/python/python-authority-v1.json`
- `tools/certify_python_authority.py`
- `tests/runtime/test_python_authority.py`
- `.github/workflows/python-authority.yml`

### Critical authority distinction

The repository currently has two different Rust facts which must never be collapsed into one number:

```text
Rust native implementation coverage = 245/245
Rust production promotion authority = 174/245
Remaining parity/promotion set        = 71
Remaining owned                       = 71
Unowned                               = 0
Atomic production target              = 245
```

`dual-engine-public-surface-v2.json` represents native **implementation coverage**.

`python-behavior-freeze-v1.json`, `python-phase1-acceptance-v1.json`, and `phase2-rust-migration-matrix-v1.json` preserve the independent **production-promotion boundary** at 174/245.

The Python authority certifier explicitly enforces this split.

### `python_authority_v1` current verification status

- Contract JSON written.
- Certifier written.
- Regression tests written.
- Immutable-pin GitHub Actions workflow written.
- Duplicate 245-route / 71-route identity lists explicitly forbidden.
- Canonical route identity remains `tools/report_missing_native_public_routes.py`.
- Python execution owner expectation remains 245.
- Rust implementation expectation is 245 with 0 implementation-missing routes.
- Rust production-promotion expectation remains 174 with 71 remaining.
- Rust resume remains forbidden until `PYTHON_COMPLETE`.
- GitHub `Python Authority` run exists on the stacked PR and is currently queued.
- **Do not mark `python_authority_v1` complete until that exact-head run passes and the final manifest is clean.**

## Rust status

```text
FEATURE DEVELOPMENT: FROZEN
IMPLEMENTATION COVERAGE: 245/245
PRODUCTION PROMOTED: 174/245
REMAINING PROMOTION/PARITY: 71
```

No new Rust feature implementation, Remaining-71 production promotion, or native counter change is authorized during the Python-first phase.

## Completed this session

- [x] Closed the stale `MANIFEST.sha256` problem on PR #132.
- [x] Removed the temporary manifest-finalizer workflow from the final PR #132 tree.
- [x] Verified the final manifest blob matches the previously validated provenance candidate.
- [x] Retriggered final exact-head #132 CI without changing the final tree.
- [x] Created `agent/python-authority-v1` from the exact #132 final tree.
- [x] Added the Python authority contract.
- [x] Added the Python authority certifier.
- [x] Added Python authority regression tests.
- [x] Corrected the implementation-vs-promotion semantic split.
- [x] Added immutable-pinned Python Authority CI.
- [x] Opened stacked draft PR #134 for real GitHub-runner certification.

## Verified

- PR #132 final tree has no temporary finalizer file.
- PR #132 manifest contains the new hashes for the repository-hygiene checker and its regression test.
- Final PR #132 manifest blob is `8d572679be28c89d05edf89e2edb6e0081199e38`.
- `dual-engine-public-surface-v2.json` reports 245 Rust native implementations and zero missing implementation routes.
- Python behavior/Phase 1 contracts still grant no Rust promotion credit and freeze production promotion at 174.
- Phase 2 baseline remains 174 promoted / 71 remaining / 71 owned / 0 unowned / atomic target 245.
- PR #134 is stacked on #132 and contains only the authority milestone relative to that base.

## Blockers

1. GitHub-hosted Actions jobs are currently spending substantial time queued.
2. PR #132 cannot be merged until its exact-head release/Python gates execute and pass.
3. `python_authority_v1` cannot be marked complete until its exact-head authority workflow passes and its final manifest is refreshed/validated.
4. Connector currently exposes workflow inspection and rerun actions but not a general queued-run cancel operation, so queued Rust differential noise cannot be directly purged from this session.

## Next exact task

```text
1. Observe PR #132 Release Main Merge Gate + Release Package Provenance + Python authority/freeze/reference gates.
2. If a final-head gate fails, diagnose that exact failure and repair only the real root cause.
3. When #132 is green, merge #132 and re-read main.
4. Retarget/reconcile PR #133 and PR #134 onto the new main.
5. Run Python Authority exact-head certification on #134.
6. Refresh #134 MANIFEST.sha256 only after the authority tree is final.
7. Mark python_authority_v1 complete only after exact-head PASS.
8. Then and only then begin capability_completeness_registry_v1.
```

## Required continuation instruction

```text
Continue Syntavra in PYTHON-FIRST mode from docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md.
Do not resume Rust feature development.
Do not interpret 245/245 Rust implementation coverage as 245/245 production promotion.
Production authority remains frozen at 174/245 until Python COMPLETE and the later atomic promotion gate.
Finish the first unchecked exact task and update this checkpoint before moving on.
```
