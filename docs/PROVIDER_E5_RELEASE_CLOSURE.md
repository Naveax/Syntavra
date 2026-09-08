# Provider E5 + Release / Completion Closure

Status: IMPLEMENTED PROOF PIPELINE / LIVE E5 EVIDENCE STILL EXTERNAL

This closure hardens four previously weak scorecard areas without upgrading claims beyond the evidence actually present.

## 1. Real provider proof, E5

Canonical implementation:

- `syntavra_runtime/provider_e5.py`
- `tools/certify_provider_e5.py`
- `benchmarks/token_economy_provider_replay.py`
- `tests/runtime/test_provider_e5.py`
- `.github/workflows/provider-e5-proof.yml`

E5 is now independent from whether Syntavra wins. A replay is E5-valid only when the complete frozen B0-B9 schedule has provider-observed paired results, at least three repetitions per workload variant, exact pair identity, valid provider/request/response/usage receipt hashes, positive provider quota/cost observations, one provider/model/reasoning/context/hardware identity, zero hardened-comparator identity/receipt/invalid errors, and failure-inclusive result retention.

A valid E5 run scores 9.3 in the E5 certifier. This is a proof-quality score, not a claim that a live E5 run has already happened. Pull-request CI proves the certifier semantics offline. Live E5 evidence is created only by the manual `Provider E5 Proof` workflow using a real two-arm configuration and provider credentials exposed only to the execution step.

## 2. External superiority

External superiority is deliberately a second gate. E5 validity is necessary but not sufficient.

- `PAIRED_PROVIDER_SUPERIORITY_PROVEN` requires an E5-valid replay plus `HardenedSignalBench.compare` claimability, a 95% confidence lower bound above 1.0, non-inferior pass rate, failure-inclusive quota accounting, and no identity, receipt, security, verifier-skip, or comparator invalidity.
- `5X_PAIRED_PROVIDER_SUPERIORITY_PROVEN` additionally requires the 95% confidence lower bound, median successful-pair ratio, and failure-inclusive efficiency ratio all to be at least 5.0.
- The claim is scoped to the frozen baseline/candidate pair. It is not universal market superiority.

A proved scoped superiority run scores 9.3; a proved 5x scoped result scores 9.7. Without live E5 data the public claim remains `EXTERNAL_SUPERIORITY_NOT_PROVEN`.

## 3. Release hygiene

The release authority no longer trusts a checked-in SHA-256 snapshot as if it could certify the exact commit that changed after the snapshot was written.

`tools/certify_release_integrity.py` binds release evidence to:

- exact Git HEAD,
- exact Git tree,
- clean relevant tracked/untracked state,
- deterministic SHA-256 manifest generated from the tracked exact-head source inventory,
- an uploaded machine-readable release-integrity receipt and generated manifest artifact.

`MANIFEST.sha256` remains a legacy transparency snapshot. Release and completion workflows temporarily generate the exact current manifest for the existing repository validator, run `refresh_manifest.py --check` and `tools/validate.py`, then restore the checked-in snapshot before the final clean-tree gate. Therefore legacy contracts that require manifest verification remain enforced while stale snapshot bookkeeping can no longer masquerade as the release authority.

A clean exact-head release-integrity receipt scores 9.4.

## 4. Completion / phase exit

The Python completion path now composes:

1. Linux exact-head platform smoke,
2. Windows exact-head platform smoke,
3. completion contract regression,
4. exact-head release-integrity certification,
5. deterministic current-tree manifest validation,
6. aggregate repository validation,
7. release smoke validation,
8. machine-readable completion certificate,
9. final exact clean-head enforcement.

The post-completion gate uses the same exact-head release-integrity authority instead of failing solely because a checked-in snapshot predates the candidate changes.

## Claim boundary

This work improves proof machinery and release/completion correctness immediately. It does **not** manufacture real provider receipts, provider billing observations, or an external-superiority result. Those states are upgraded only after a protected live E5 run produces evidence that passes the same certifiers committed here.
