# U5-P0-03 Future-Reuse Retention Predictor

Date: 2026-09-10  
Status: `IMPLEMENTATION CANDIDATE / SHADOW-FIRST / EXACT-HEAD ADMISSION REQUIRED`

## Decision

U5-P0-03 is implemented as a deterministic, local prediction layer over the already-admitted U5-P0-02 drop/reacquisition receipts. It does not create a new raw-context store, semantic-state database, provider worker, or competing retention authority.

The retention score is:

`retention_value = future_use_probability * expected_reacquisition_cost_units - carry_cost_units`

A positive value may recommend `KEEP`, but the default policy is shadow-only and therefore leaves the baseline effective action unchanged.

## Frozen-trace labeling

Training/evaluation labels are derived only from U5-P0-02 accounting receipts:

- a prior `PRUNE` with a later attributed reacquisition event inside an explicitly complete frozen horizon is a positive future-use label;
- a prior `PRUNE` with no later attributed reacquisition is a negative label only when the frozen horizon is explicitly complete;
- an incomplete horizon remains unlabeled and is excluded rather than being converted into a false negative.

The predictor consumes stable reference-only `feature_key` values supplied by the caller. It does not claim ownership of semantic state and never needs raw context payloads to fit the current deterministic empirical estimator.

## Estimator

The initial estimator is deliberately small and auditable:

- Beta-smoothed empirical future-use probability;
- deterministic grouping by `feature_key`;
- expected reacquisition cost from positive observed U5-P0-02 events;
- caller-provided fallback reacquisition cost when no positive observation exists;
- zero provider calls and zero provider tokens for fit/predict;
- deterministic content-addressed fit, estimate, prediction and promotion receipts.

This is preferable to introducing a learned black box before the project has accumulated frozen counterfactual traces. A more sophisticated predictor can later replace the estimator only behind the same receipt and promotion gates.

## Hard evidence boundary

The following are pinned outside learned retention decisions:

- mandatory context;
- exact-required context;
- mandatory failure evidence;
- mandatory security evidence;
- mandatory verifier evidence.

These always resolve to effective `KEEP`. Invalidation also remains outside the learned predictor; an invalidated baseline decision is not resurrected merely because historical reuse probability is high.

## Shadow and promotion boundary

Defaults:

- `shadow_mode=true`;
- `promotion_enabled=false`.

A prediction may therefore recommend a different action while the effective action remains the baseline. Promotion additionally requires an explicit non-shadow policy and a clean evaluation gate.

The evaluation gate fails closed on:

- any mandatory-evidence miss;
- lower task-success rate;
- higher reacquisition cost;
- higher provider cost per successful task when paired provider cost measurements are available.

A failed gate is the rollback signal. No predictor recommendation is allowed to silently outrank correctness, exact recovery, security, verifier evidence or end-to-end economics.

## Provider claim boundary

Local future-use prediction is not hosted-provider savings proof. Fit and prediction report zero provider work, but a future provider-savings claim still requires equivalent frozen baseline/candidate tasks, identical verifiers and paired provider-observed receipts through the existing provider proof gates.

`provider_savings_claim=false` remains hard-coded in U5-P0-03 receipts.

## Public-surface reconciliation

U5-P0-02 added one Python runtime module and U5-P0-03 adds one more. The authoritative Python module inventory therefore moves from 232 to 234 while the public command count remains 245. The U5-P0-03 exact-head workflow runs `tools/verify_dual_engine_public_surface.py` explicitly so future internal-module additions cannot leave the contract snapshot stale without failing the dedicated admission gate.

## Admission

Exact-head admission requires `.github/workflows/u5-p0-03-future-reuse-retention-predictor.yml` to pass:

1. `tests.runtime.test_future_reuse_retention_predictor`;
2. `tools/validate_u5_p0_03_future_reuse_retention_predictor.py`;
3. dual-engine Python public-surface verification;
4. exact-head and clean-repository checks;
5. deterministic frozen-horizon label, hard-pin, shadow and promotion-gate probes.

Until that exact-head gate is green, U5-P0-03 remains an implementation candidate.

## Next canonical pass

After admission, continue `U5-P0-04 Communication Medium Router`. Reconcile existing SWIR, provider gateway, local runtime and delegation/subagent surfaces first; do not add a parallel transport authority merely to satisfy the roadmap name.
