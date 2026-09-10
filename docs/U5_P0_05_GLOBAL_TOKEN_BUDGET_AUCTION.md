# U5-P0-05 Global Token Budget Auction

Date: 2026-09-10  
Status: `IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED`

## Decision

U5-P0-05 is implemented as a decision-only allocation overlay on the existing canonical context/provider surface in `syntavra_runtime/context_governor.py`.

Reconciliation showed that a new runtime module is unnecessary. `ProviderTokenEnvelope` already owns the hard provider-visible input/output caps and admission decision. The auction consumes that admitted envelope and only recommends how its already-bounded tokens should be divided between existing execution lanes. It does not create a second provider budget, context store, reasoning engine, output renderer, or persistence layer.

## Canonical lanes

The shared provider-visible budget is divided into five lanes:

- input: `retrieval`;
- input: `instructions_context`;
- input: `schema_tool_output`;
- output: `reasoning`;
- output: `output`.

The input lanes must share exactly the envelope's `provider_input_budget_tokens`. The output lanes must share exactly `provider_output_budget_tokens`.

The fixed per-lane baseline must exactly partition those same caps. This prevents a smaller total budget from masquerading as a better auction policy.

## Hard minima and necessity authority

Hard minima are reserved before any marginal bid is considered.

The summed hard minima on each provider side must:

1. fit inside the corresponding `ProviderTokenEnvelope` cap; and
2. be at least the envelope's mandatory token total for that side.

This preserves the existing necessity boundary for user instructions, security policy, exact source, current failures, verifier evidence, selected schemas, dependencies, recovery handles and requested output. The auction cannot use a high estimated value elsewhere to erase irreducible evidence.

`ProviderTokenEnvelope.provider_call_admissible=false` is terminal for this component. The allocator cannot override it.

## Marginal-value auction

Every optional tranche is represented by a `MarginalTokenBid` containing:

- lane;
- bounded token count;
- estimated verified gain;
- evidence reference;
- stable ordinal.

Within each lane, marginal value density must be non-increasing. Across lanes, bids are ordered deterministically by:

`estimated_verified_gain / token -> lane -> ordinal -> bid_id`

The allocator seeds hard minima and then assigns each remaining token tranche to the highest-value eligible lane without exceeding the lane maximum or provider-side envelope cap.

The estimated gain is explicitly an allocation signal, not a provider-savings claim.

## Shadow and offline boundary

U5-P0-05 accepts only `shadow` and `offline` modes.

In `shadow` mode:

- the auction recommendation is computed and receipted;
- the effective allocation remains the fixed baseline;
- no live provider behavior changes.

In `offline` mode the recommendation may become the effective allocation for replay/evaluation, but it still does not mutate live provider routing.

Any `live` mode is rejected. A later promotion requires its own evidence and authority decision.

## Existing owner reconciliation

`ProviderTokenEnvelope` remains the only hard provider budget/admission authority.

`pack_context` and the existing retrieval/context runtime continue to own actual context selection. The auction only supplies a lane allowance.

`OutputGovernor` continues to own output shaping/rendering. The auction does not truncate or render output itself.

The current runtime has no separate literal `ReasoningEffortGovernor` or `TokenPerSuccessOptimizer` class matching the roadmap labels. U5-P0-05 therefore does not invent replacement subsystems merely to satisfy those names. Reasoning remains an allocation lane, while existing observability/provider-receipt machinery remains the measurement authority.

`ObservabilityAttribution`, provider usage receipts and token-attribution receipts remain external authorities. No parallel persistent evidence store is introduced.

## End-to-end overhead accounting

The allocator records attributable provider-visible overhead after an allocation decision for:

- retry;
- recall;
- repair;
- fallback.

Those tokens count even when they make the apparent first-pass allocation look smaller.

An overhead event becomes provider-verified only when its provider usage receipt and token-attribution receipt hashes are supplied as a pair. Baseline and optimized execution receipt hashes are likewise pairwise required.

The accounting receipt reports the fixed baseline total, recommended total, overhead by kind/lane, provider-verified overhead, and an estimated net token delta including overhead.

## Claim boundary

`provider_savings_claim=false` is hard-coded.

Even paired receipt hashes at this component do not prove a savings claim by themselves. Hosted-provider savings require equivalent frozen baseline/optimized tasks, identical verifiers/security gates, actual provider-observed receipts and the compound TE-U36 proof path.

No component percentage is arithmetically added to another component's claimed reduction.

## Receipts and rollback

Auction decisions and overhead events are content-addressed. Receipt verification fails on mutation.

The rollback path is the fixed per-lane baseline:

- shadow mode already executes the fixed baseline;
- offline recommendations do not alter live behavior;
- a failed or non-inferior-gate candidate can be discarded without modifying the `ProviderTokenEnvelope` authority.

## Public-surface reconciliation

U5-P0-05 extends `context_governor.py` rather than creating another runtime module or CLI command.

The authoritative public surface therefore remains:

- Python runtime modules: `234`;
- public commands: `245`.

## Admission

Exact-head admission requires `.github/workflows/u5-p0-05-global-token-budget-auction.yml` to pass:

1. `tests.runtime.test_token_budget_allocator`;
2. `tools/validate_u5_p0_05_global_token_budget_auction.py`;
3. dual-engine Python public-surface verification;
4. exact-head and clean-repository checks;
5. hard-minimum, fixed-baseline, shadow/offline, receipt and overhead-accounting probes.

Until that exact-head gate is green, U5-P0-05 remains an implementation candidate.

## Next canonical pass

After admission, continue `U5-P1-03 Reacquisition-Aware Compaction Scheduler`.

That pass must reuse the existing reacquisition-tax/future-reuse receipts, context-pressure owners and prompt-cache economics rather than creating a parallel context economics model.
