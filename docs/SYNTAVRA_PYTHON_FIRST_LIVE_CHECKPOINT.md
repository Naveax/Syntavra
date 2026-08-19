# Syntavra Python-First Live Checkpoint

Updated: **2026-08-19**

This file is the volatile continuation authority. Historical milestones live in `SYNTAVRA_PYTHON_FIRST_CHECKPOINT.txt`; the capability registry is the machine-readable lifecycle authority.

## Current admitted base

- `main` before Memory work: `6120ea9d074b0cac0e880e3e9dbf873d1faaec58`
- Context Reset / Handoff v1: runtime merged through PR #148; lifecycle certified through PR #152.
- Python feature authority: active.
- Python COMPLETE: false.
- Rust feature development: frozen.
- Rust production promotion: 174/245.
- Remaining Rust parity/promotion set: 71.

## Current implementation

### PR #151 — Memory Retrieval v1

- Branch: `agent/memory-retrieval-v1`.
- Existing `MemoryIntelligenceStore` SQLite authority is reused.
- Existing `SessionMemory` exact hash-chain authority is reused.
- No parallel persistent memory database is introduced.
- Episodic, semantic, procedural, project, user and temporal memory scopes are represented.
- Project/user/session retrieval isolation fails closed.
- Exact recovery requires a visible scope.
- Lifecycle mutation, forgetting and consolidation require exact scope ownership.
- Session retrieval binds the session's recorded user to the requested memory scope.
- Provenance is mandatory.
- Conflicts remain explicit; supersession is explicit.
- Lifecycle relation sets are part of memory identity.
- Consolidation preserves parent lineage.
- Forgetting is logical and durable: inactive for retrieval, exact payload preserved, duplicate remember does not silently reactivate it.
- Invalid observations are excluded from active retrieval.
- Hybrid BM25/vector retrieval uses deterministic query expansion and importance/confidence/validity/recency reranking.
- Retrieval and cross-agent handoff emit deterministic content-addressed receipts.

## Verification state

- The original Memory Retrieval implementation passed its dedicated exact-head workflow (7/7 initial regressions).
- Release Main on that pre-seal head passed runtime/certifier/freeze checks and stopped only at the intentionally stale manifest.
- Admission review then found and hardened cross-scope relation mutation/recovery/forgetting, session-user binding, invalid-memory retrieval and silent reactivation edges before merge.
- Final exact-head admission gates must pass on the permanent sealed tree before PR #151 can merge.

## Current lifecycle repair

- `context_reset_handoff_v1` advances from stale `partial` metadata to `certified`.
- `memory_retrieval_v1` is recorded as `implemented` pending final exact-head admission/merge.
- Machine-readable milestone order now continues through Epistemic Safety, Cache/Provider/Budget, Output Intelligence, Host Adapter Conformance, Observability Attribution and SignalBench.
- Capability completeness enforces the full order; omitted future milestones cannot accidentally yield Python COMPLETE.

## Next exact task

1. Pass final exact-head Memory Retrieval, Capability Completeness, Context Reset, Rust Freeze, Release Main and Package Provenance gates.
2. Merge PR #151 only after all load-bearing checks pass.
3. Re-read fresh `main`.
4. Begin `epistemic_safety_v1`; reuse existing security scan, trust/taint, claim governance, capability authorization and Adaptive Context Policy primitives instead of duplicating them.

## Required continuation instruction

```text
Continue Syntavra in PYTHON-FIRST mode from docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md.
Cross-check contracts/python/capability-completeness-registry-v1.json before choosing work.
Do not resume Rust feature development or alter the 174/245 production-promotion boundary.
Do not start a later milestone while the first non-admitted canonical milestone is still open.
When CI is active, track the existing run instead of creating a duplicate; continue independent work while it runs.
```
