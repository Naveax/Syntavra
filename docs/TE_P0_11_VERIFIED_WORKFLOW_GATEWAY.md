# TE-P0-11 Verified Workflow Gateway Replay

Status: IMPLEMENTED CANDIDATE / EXACT-HEAD CI REQUIRED  
Authority: `docs/plans/SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1.md` TE-P0-11  
Core prerequisite: `syntavra_runtime/verified_workflow.py`

## Purpose

This milestone connects promoted verified read-only workflows to the real
`AgentRuntime -> GatewayPatchProvider -> AutonomousCodingAgent` product path.
It eliminates repeated provider planning calls only when the existing
TE-P0-10 compatibility authority proves the runtime state compatible.

No new capability namespace or parallel macro database is introduced.

## Runtime path

Eligible execution is:

`verified macro -> local action queue -> GatewayPatchProvider existing dispatch -> existing exact evidence ingestion -> provider only for residual patch planning -> fresh verifier`

The local queue intercepts `_call_model()` only for read-only actions. The
normal `GatewayPatchProvider.propose()` implementation still executes
`search`, `search_reduce`, `search_inspect`, `inspect`, `diff`, `impact`,
`verifiers`, and `run_verifier`. Therefore macro replay does not create a
second tool execution or evidence policy.

`edit` and `patch` never enter a macro. A provider still owns the residual
mutation proposal in this milestone.

## Compatibility and invalidation

The gateway layer starts with the exact TE-P0-10 `InferenceSkipIdentity`.
Instruction identity is excluded by the verified-workflow core because the
instruction is an explicit macro parameter.

The compatibility identity still binds:

- repository state;
- verifier identity;
- dependency state;
- toolchain;
- environment;
- provider/security policy;
- tool schema;
- verifier contract.

The gateway integration additionally hashes all runtime sources that can
change replay semantics:

- `verified_workflow.py`;
- `verified_workflow_extension.py`;
- `agent_runtime.py`;
- `autonomous_agent.py`;
- `agent_retrieval.py`.

That runtime hash is mixed into the policy component. An implementation
change therefore cannot silently reuse an old macro.

## Conservative parameterization

The first product lane exposes exactly one parameter: `instruction`.

A provider-planned read-only graph becomes experience evidence only when the
exact task instruction appears as an exact action value. There is no
substring replacement, fuzzy task matching, semantic guessing, or inferred
parameter boundary.

If the graph cannot be parameterized exactly, it is ignored rather than
promoted.

## Promotion and replay

Promotion still requires at least two independent verified experiences with
distinct task-reference hashes and distinct verifier-receipt hashes.

Replay is allowed only on:

- attempt 1;
- no previous failure;
- clean current diff;
- complete TE-P0-10 compatibility identity;
- exactly one active compatible macro.

Ambiguity falls back to normal provider planning.

Each replayed planning action increments `provider_calls_avoided`. This is
local call-avoidance accounting, not a provider-billing claim.

## Fresh verification and quarantine

A replay does not become successful merely because its local actions ran.

The resulting patch still passes the normal primary verifier and all enabled
post-verifiers. The fresh sandbox execution receipt is hashed into the macro
execution receipt.

A read-only replay action failure is safe to retry through normal provider
planning because no replayed action can mutate the repository. The failed
macro is quarantined first.

A final verifier failure also quarantines the macro. Quarantine is appended
to the same `SessionMemory` hash chain as experiences, promotions and
execution receipts. A later promotion backed by new independent verified
experience may reactivate the macro.

Persisted quarantine reasons contain only bounded error classifications, not
exception text or raw evidence.

## Claim boundary

This milestone can prove that eligible planning calls were not made because
the replay is local and the counter is attached to the execution receipt.

It does **not** prove provider-billed token savings. That claim remains
closed until paired provider-observed receipts under equivalent frozen
tasks/verifiers demonstrate the savings.

## Acceptance

The dedicated exact-head workflow must prove:

1. package bootstrap installs the integration once;
2. two independent verified traces promote one workflow;
3. a third compatible task replays the read-only graph and avoids the
   corresponding provider planning call;
4. replay still uses the existing gateway dispatch;
5. a fresh verifier is required;
6. verifier failure quarantines the macro;
7. quarantine blocks reuse until a later independent promotion;
8. runtime hash drift disables the old macro;
9. the repository remains clean at the exact candidate head.
