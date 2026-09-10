# TE-P0-10 Verifier-Gated Inference Skip Cache V2

Updated: **2026-09-08**  
Status: IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED

## Reconciliation classification

`EXISTS + HARDEN + CERTIFY`

Canonical owners remain:

- `syntavra_runtime/inference_skip_cache.py`
- `syntavra_runtime/autonomous_agent.py`

The existing implementation already provided exact cache replay, provider-call bypass, post-replay verification, patch-integrity invalidation, dirty-worktree fail-closed behavior and single-verifier admission.

## V2 exact identity

`inference-skip-v2` binds the cache key to all of these classes:

1. normalized task family;
2. exact repository/worktree state;
3. normalized instruction;
4. verifier argv identity;
5. mode + caller policy + cache/runtime contract;
6. project dependency manifests plus installed distribution versions;
7. resolved verifier toolchain executable identity;
8. Python/platform environment identity;
9. Syntavra provider-visible tool-schema implementation identity;
10. Syntavra sandbox/security implementation identity;
11. verifier contract implementation identity.

The implementation-derived hashes are computed locally from the installed Syntavra runtime source files. Missing source/runtime/toolchain evidence makes identity construction fail closed rather than silently reverting to a weak key.

Explicit fingerprint overrides are supported for integration/testing, but blank overrides use the conservative local fingerprints above.

## Dependency and environment boundary

Known dependency/lock manifests are hashed together with installed distribution name/version pairs. Environment identity contains non-secret interpreter/platform properties only. No arbitrary environment variables or credentials are persisted.

The verifier toolchain executable is resolved and content-hashed without executing it, avoiding side effects from probing arbitrary custom test runners.

## Prevented false semantic hits

A same-project + same-normalized-instruction cache row is **not** reused when any exact identity component differs.

Such near misses are persisted in `inference_skip_preventions` with hashed identities and reason classes such as:

- `REPOSITORY_FINGERPRINT_MISMATCH`
- `VERIFIER_HASH_MISMATCH`
- `POLICY_HASH_MISMATCH`
- `DEPENDENCY_HASH_MISMATCH`
- `TOOLCHAIN_HASH_MISMATCH`
- `ENVIRONMENT_HASH_MISMATCH`
- `TOOL_SCHEMA_HASH_MISMATCH`
- `SECURITY_HASH_MISMATCH`
- `VERIFIER_CONTRACT_HASH_MISMATCH`

No raw instruction or secret environment material is written to the prevention ledger.

Corrupt exact hits and cached replay apply/verifier failures are also classified as rejected-reuse events.

## Existing zero-provider behavior retained

On an exact verified cache hit, `AutonomousCodingAgent` still:

1. bypasses `PatchProvider.propose(...)`;
2. assigns zero estimated provider tokens/cost to the replay;
3. applies the exact cached patch in the isolated workspace;
4. reruns the complete admitted verifier;
5. returns success only if that verifier passes;
6. invalidates the cache row if apply or verification fails.

Therefore eligible repeated deterministic tasks may still consume zero provider inference calls. This is an internal execution fact, not a claim about every workload.

## Migration

The cache contract version changes from `inference-skip-v1` to `inference-skip-v2`, so legacy keys cannot collide with V2 keys. Existing SQLite databases are migrated additively with `identity_json` and the prevention ledger; old rows remain inspectable but are not silently upgraded into V2 reuse authority.

## Regression coverage

Existing:

- `tests/runtime/test_inference_skip_cache.py`
- `tests/runtime/test_recovery_to_99_property_matrix.py`

New:

- `tests/runtime/test_inference_skip_cache_v2.py`

The V2 suite proves fingerprint-class presence, explicit dimension mutation, dependency mutation, exact hit retention, semantic near-miss recording and patch-integrity rejection recording.

## Claim boundary

This pass certifies deterministic identity/replay mechanics only. Provider-billed token savings still require the existing paired provider-observed evidence gates. Cache-hit counters are not substituted for provider usage receipts.

## Next

After exact-head admission, reconcile production receipts with prevention statistics and continue with TE-P0-11 Workflow Skill / Macro Compiler while keeping the TE-P0-08 gateway `search_reduce` integration open until it can be changed safely as a bounded patch.
