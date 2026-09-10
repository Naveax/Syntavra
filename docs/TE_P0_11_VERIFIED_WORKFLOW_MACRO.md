# TE-P0-11 Verified Workflow / Macro Compiler Core

Status: IMPLEMENTED CORE / GATEWAY REPLAY PENDING  
Authority: `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md`

## Purpose

TE-P0-11 removes repeated provider planning work only after the same read-only
tool graph has been independently verified more than once. It is deliberately
not a semantic patch cache. TE-P0-10 remains the exact-state patch/inference
skip owner.

The core implementation is `syntavra_runtime/verified_workflow.py`.

## Canonical owner reconciliation

No new execution engine or parallel workflow database is introduced.

- planning remains owned by `adapter_platform.CodingAgent.plan`;
- typed execution remains owned by `ProgrammaticExecutionPlane`;
- verified experience and promotion provenance use `SessionMemory`;
- compatibility reuses TE-P0-10 `InferenceSkipIdentity` components;
- deterministic policy identity remains owned by `DeterministicPolicySnapshot`.

`SessionMemory` is the exact hash-chain authority for experience, promotion and
execution events.

## Promotion gate

A workflow is promotable only when all of the following hold:

1. every action is in the explicit read-only allowlist;
2. `edit` and `patch` never appear in the macro body;
3. parameters are explicit exact-value substitutions, not substring guesses;
4. at least two unique verified experiences share the exact same parameterized template;
5. those experiences have at least two distinct task-reference hashes;
6. those experiences have at least two distinct verifier-receipt hashes.

Recording the same successful run twice cannot manufacture a promotion.

## Compatibility and invalidation

`WorkflowCompatibilityIdentity` reuses TE-P0-10 identity components except the
instruction hash. The instruction is excluded because it is a parameter to the
reusable workflow rather than an invariant.

Compatibility includes project, repository state, verifier, policy, task family,
dependencies, toolchain, environment, tool schema, security and verifier
contract fingerprints. Any incompatible identity resolves to no macro. The
first implementation is intentionally conservative: repository state remains
part of the identity.

## Ambiguity

Automatic selection is allowed only when exactly one promoted macro is
compatible. Multiple compatible workflows fail closed unless an exact
`macro_id` is supplied.

## Execution receipt

A successful macro execution cannot be recorded unless it has been freshly
re-verified. The receipt binds macro identity, compatibility identity, bound
parameter/action hashes, fresh verifier receipt, provider calls avoided and
executed action count.

Provider calls avoided is measurement data, not automatically a provider-billed
savings claim.

## Claim boundary

This milestone proves core promotion/reuse mechanics only. It does not yet route
`GatewayPatchProvider` through promoted macros and therefore does not claim
provider-call or provider-token savings.

The next milestone is gateway replay through the existing action/evidence path,
with fail-closed fallback and the normal final verifier still mandatory.
