# U5-P0-01 Zero-Token Memory Engine

Date: 2026-09-09  
Status: `IMPLEMENTATION CANDIDATE / EXACT-HEAD ADMISSION REQUIRED`

## Decision

U5-P0-01 is implemented as a thin offline adapter over the existing owners. `PersistentMemory` remains authoritative for exact raw memory, deterministic local retrieval and graph edges. `EvidenceStore` remains authoritative for encrypted content-addressed evidence bytes, references and retention/GC. No second memory database or provider-backed maintenance path is introduced.

## Hardening

- Memory text is stored and hashed exactly as supplied. Whitespace normalization is used only to reject empty values.
- Exact duplicate inputs remain idempotent, while raw-distinct variants such as `"x"` and `" x "` remain distinct records.
- Evidence handles are attached to memory provenance and retained with deterministic `memory:<project>:<user>:<memory_id>` references.
- TTL maintenance first preflights referenced evidence metadata, then purges expired authoritative memory, releases evidence references only for records actually deleted, and finally runs the existing reference-aware `EvidenceStore` GC. This ordering fails toward retained/orphan references rather than prematurely removing evidence protection from a still-live memory record.
- If an expired memory was the target of `superseded_by`, its predecessor is reactivated before the target is deleted so SQLite foreign-key integrity and authoritative fallback are both preserved.
- FTS5 can be rebuilt entirely from authoritative SQLite memory. Systems without FTS5 continue to use the existing authoritative `LIKE` fallback.
- Ingest, search, graph link, maintenance and reindex receipts explicitly report zero provider calls and zero provider tokens.
- LLM repair is outside the default path. Any future repair path must remain an explicit escape hatch and count provider work end-to-end.

## Ownership boundary

`PersistentMemory` owns:

- exact raw memory text;
- scoped deterministic dedup identity;
- local lexical/FTS retrieval;
- graph relations and supersession;
- memory expiry metadata.

`EvidenceStore` owns:

- exact encrypted evidence bytes;
- content-addressed evidence identity;
- deterministic memory references;
- legal holds, TTL metadata and reference-aware GC.

`ZeroTokenMemoryEngine` only coordinates these owners. It is not a third persistence authority.

## Claim boundary

This slice hardens the zero-provider default memory path. It does not claim that every final task can be solved without a model, and it does not convert local zero-token maintenance into a hosted-provider savings claim. External savings still require equivalent frozen tasks and provider-observed receipts.

## Admission

The implementation is not promoted merely because the code exists. Exact-head admission requires the dedicated `U5-P0-01 Zero-Token Memory Engine` workflow to pass the runtime regressions and `tools/validate_u5_p0_01_zero_token_memory_engine.py`, with a clean repository check. Until that exact-head gate is green this remains an implementation candidate.
