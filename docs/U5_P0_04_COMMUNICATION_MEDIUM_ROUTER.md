# U5-P0-04 Communication Medium Router

Date: 2026-09-10  
Status: `IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED`

## Decision

U5-P0-04 is implemented inside the existing delegation owner `syntavra_runtime/subtask_router.py`. No parallel transport runtime or new provider authority is created merely to satisfy the roadmap name.

The router is decision-only. It chooses a faithful handoff medium and emits a deterministic content-addressed decision receipt. Actual transport remains with the existing runtime/provider owners.

## Endpoint capability matrix

Every endpoint explicitly declares its supported handoff media, ownership boundary and security domain. Text is mandatory as the final serialized fallback.

Supported media are:

1. silence when no handoff is required;
2. stable semantic handle;
3. opaque typed receipt reference;
4. SWIR compact structured text;
5. natural text;
6. embedding, hidden-state or KV references only for explicitly compatible local/owned endpoints.

Provider aliases never establish locality. This matters because hosted services and local runtimes can share OpenAI-compatible request shapes. A hosted endpoint is validated through `ProviderGateway`, but the communication router does not duplicate ProviderGateway's provider table.

## Fidelity and cost routing

Required fidelity is a hard gate. Exact-required handles and receipt references need an exact recovery reference. Exact-required SWIR handoffs require exact recovery on every atom and remain subject to the existing `SemanticWireCompiler` no-expansion gate.

Among faithful candidates, verified end-to-end cost evidence wins. Nominal payload size alone cannot win a route. When no verified total-cost evidence is available, the router falls back conservatively through handle, receipt, SWIR and text. Latent media are never selected from an unverified size guess.

This allows a larger text payload to beat a smaller SWIR payload when measured reacquisition, decoding, retry or downstream work makes SWIR more expensive overall.

## SWIR and receipt authority

SWIR grammar, tokenizer accounting, exact-recovery requirements and no-expansion behavior remain owned by `SemanticWireCompiler`. The router consumes only its admitted `WirePacket` result.

Delegation receipts are not redefined. `AutomaticSubtaskDelegator.receipt_hash` can be carried as an opaque typed `ReceiptReference`; the router does not assign new meaning to the receipt body.

## Local latent/KV boundary

Embedding, hidden-state and KV handoffs require all of the following:

- both endpoints are explicitly `local` or `owned`;
- both advertise the medium;
- both declare the same non-empty latent compatibility id;
- both are in the same security domain;
- verified end-to-end cost and fidelity evidence exists for that medium.

Hosted endpoints fail closed if they advertise latent/KV media. A provider name such as an OpenAI-compatible alias is never used to infer ownership.

## Provider-visible accounting

The route receipt can record baseline and selected provider-visible tokens/calls. Avoided work is non-zero only when the caller supplies paired provider-observed baseline and selected receipt references. Unpaired counters remain visible but contribute zero avoided work.

`provider_savings_claim=false` remains hard-coded for U5-P0-04. Compound hosted-provider savings still require equivalent frozen tasks, identical verifiers/security gates and the later provider certification pass.

## Security and fallback

Latent state never crosses a security-domain boundary. Serialized text fallback is mandatory on every endpoint. If SWIR loses its tokenizer no-expansion gate, the router preserves the natural text route instead of forcing a compact form.

The router owns only its deterministic route-decision receipt. It does not own transport, raw semantic state, ProviderGateway capabilities, SWIR grammar or delegation receipt semantics.

## Public-surface reconciliation

U5-P0-04 extends the existing `subtask_router.py` owner and therefore adds no Python runtime module and no CLI command. The authoritative public-surface snapshot remains 234 Python modules and 245 public commands.

## Admission

Exact-head admission requires `.github/workflows/u5-p0-04-communication-medium-router.yml` to pass:

1. `tests.runtime.test_communication_medium_router`;
2. `tools/validate_u5_p0_04_communication_medium_router.py`;
3. dual-engine Python public-surface verification;
4. exact-head and clean-repository checks;
5. capability, fidelity, cost, security, SWIR fallback and provider-accounting probes.

Until that exact-head gate is green, U5-P0-04 remains an implementation candidate.

## Next canonical pass

After admission, continue `U5-P0-05 Global Token Budget Auction`. Reconcile ProviderTokenEnvelope, ContextAdmissionController, ReasoningEffortGovernor, OutputGovernor and TokenPerSuccessOptimizer before creating any new allocation authority.
