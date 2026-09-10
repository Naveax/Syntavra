# U5-P0-02 Reacquisition Tax Governor

Date: 2026-09-09  
Status: `IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED`

## Decision

U5-P0-02 is implemented inside the existing `syntavra_runtime.context_governor` authority. It consumes and reseals `AdaptiveContextPolicy` receipts and remains compatible with `ContextDecisionTrace`; it does not create a second context store, a second policy engine, or a hidden provider path.

The default rule is:

`retain when expected_reacquisition_cost > expected_carry_cost`

with hard exceptions for security and invalidation. Missing reacquisition estimates fail closed to retention by default.

## Accounting

`ContextCostEstimate` carries expected retained-context token cost; expected retrieval, tool and provider tokens; optional latency and monetary cost; tokenizer method and exact-target-tokenizer labels; optional paired provider-observed baseline receipt hashes; and mandatory/invalidation/security state.

`ReacquisitionTaxGovernor` records deterministic retention decisions and later reacquisition events. A later `read`, `search`, `retrieve`, `tool`, `provider` or `reconstruct` event is attributed to the latest prior prune decision for the same context identity.

The accounting receipt reports counterfactual retention tokens, reacquisition retrieval/tool/provider tokens, latency, monetary cost, accounted net token/cost delta, provider-evidence completeness and unattributed events. Unattributed later reads are not falsely charged to pruning.

## Correctness and invalidation

- Mandatory or exact-required context is retained.
- If reacquisition is expected to cost more than carrying context, pruning is refused.
- Security-denied or explicitly security-forced context can be dropped even when reacquisition would be expensive.
- Invalidated context can be dropped because stale context is not a valid retention target.
- If retention imposed by the tax governor makes provider context exceed the existing hard budget, the session decision becomes `ABSTAIN`; the governor does not silently violate the budget.
- `enabled=false` is the rollback path. Economic pruning becomes safe-retain while security/invalidation forced drops remain active.

## Provider-claim boundary

All observed reacquisition work is counted even when hosted-provider receipts are unavailable. Positive provider-token events without paired provider-usage and token-attribution receipt hashes are marked not provider-verified.

A provider-verified accounting result additionally requires paired baseline receipt hashes for retained-context carry cost. Even then this component sets `provider_savings_claim=false`: a hosted-provider savings claim still requires equivalent frozen baseline and optimized tasks/verifiers through the existing provider proof gates.

## Tokenizer boundary

Token estimates carry both a counting method label and an `exact_target_tokenizer` flag. Latency and monetary costs are not silently converted to tokens. Their token-equivalent weights default to zero and must be explicitly configured if a workload wants a combined cost-unit objective.

## Admission

Exact-head admission requires `.github/workflows/u5-p0-02-reacquisition-tax-governor.yml` to pass:

1. `tests.runtime.test_reacquisition_tax_governor`;
2. `tools/validate_u5_p0_02_reacquisition_tax_governor.py`;
3. exact-head and clean-repository checks;
4. deterministic receipt, counterfactual baseline and `ContextDecisionTrace` compatibility probes.

Until that exact-head gate is green, this remains an implementation candidate.

## Next canonical pass

After admission, continue TE-U31 with `U5-P0-03 Future-Reuse Retention Predictor`. The predictor must start shadow-first and reuse these drop/reacquisition receipts rather than inventing parallel economics.
