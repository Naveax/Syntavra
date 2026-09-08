# TE-P0-01 Pre-Model Ingestion Fold Gate

Status: IMPLEMENTED / BRANCH-CANDIDATE / CLAIM-BOUNDARY PRESERVED  
Date: 2026-09-08  
Authority lineage: `SYNTAVRA_TOKEN_ECONOMY_EXECUTION_BACKLOG_V1` -> current token-floor checkpoint

## Purpose

Close the first deterministic token-economy foundation without inventing a new CAP namespace. Tool evidence must be made exact/recoverable before provider admission, first visibility must be bounded, mandatory failure evidence must receive a larger lease without becoming unbounded, and exact recall must not be able to re-flood provider context.

## Canonical owner reconciliation

- `syntavra_runtime.tool_externalization.ToolOutputExternalizer`: exact artifact persistence, segmentation, integrity, delta lineage, search/reveal.
- `syntavra_runtime.agent_context_runtime.ConstantContextState`: provider-admission folding, active/causal evidence state, mandatory evidence pinning, scope-bound exact recall and provider-visible byte attribution.
- `syntavra_runtime.agent_runtime.GatewayPatchProvider`: consumes `ConstantContextState` before every provider request and therefore never needs to replay raw tool history.
- `syntavra_runtime.host_output_pipeline.HostOutputPipeline`: existing host/hook/MCP exact-first interception remains a parallel ingress owner and is not replaced by this agent-path gate.

## Implemented invariants

1. When an externalizer is configured, raw tool evidence is persisted to an exact artifact before the resulting receipt can enter provider context.
2. First provider visibility records raw bytes, admitted visible bytes and avoided bytes. `first_visibility_folded=true` is emitted only when exact recovery exists and the visible body is smaller than raw evidence.
3. Mandatory failure evidence receives a larger deterministic preview lease. It remains bounded and exact-recoverable.
4. Mandatory failure/security/verifier evidence previews are never silently deleted by context-budget compaction. If those leases cannot fit, provider admission fails closed with `ContextBudgetExceeded`.
5. Exact recall is restricted to artifacts admitted in the current `ConstantContextState` scope.
6. Recall budgets are hard-capped by `recall_budget_bytes` even if a caller requests a larger page.
7. Recalled raw evidence is always wrapped as untrusted tool data so exact recovery cannot become an instruction channel.
8. Recall frequency is recorded per evidence key. Subsequent evidence for repeatedly recalled keys receives a deterministic warm lease, capped by `warm_recall_ceiling_bytes`.
9. Unchanged results remain zero-preview causal receipts and now carry explicit provider-visibility attribution.
10. Raw history remains non-replayed; active evidence plus bounded causal receipts remain the provider context authority.

## Recovery and invalidation

- Exact recovery depends on the artifact handle produced by `ToolOutputExternalizer`.
- A recall for an artifact not admitted into the current context scope fails with `PermissionError`.
- Unsupported recall lenses and invalid query recalls fail before any provider-visible evidence is produced.
- Continuation remains bounded by the externalizer continuation contract and every page is additionally capped by the context-state recall budget.
- Superseded/evicted receipts preserve artifact/exact handles in causal lineage.

## Verification

Dedicated regression coverage: `tests/runtime/test_pre_model_ingestion_fold_gate.py`.

Coverage includes:

- exact raw persistence before first visibility;
- first-visibility fold attribution;
- larger but bounded failure lease;
- scope-bound hard-capped recall;
- untrusted recall guard;
- deterministic warm-recall accounting;
- mandatory failure preview fail-closed behavior;
- zero-preview unchanged-result attribution.

## Claim boundary

This implementation is an engineering mechanism, not a provider-savings claim. Provider-token savings remain closed until paired provider-observed receipts under equivalent frozen tasks/verifiers satisfy the existing E5/external-superiority contracts. Local byte reduction and local tokenizer counts do not upgrade that claim.

## Next deterministic foundation

Proceed to `TE-P0-02 Active Context Supersession Graph`, then `TE-P0-03 Useless / No-Change Result Elision`, without reopening TE-P0-01 unless CI or exact-head evidence finds a regression.
