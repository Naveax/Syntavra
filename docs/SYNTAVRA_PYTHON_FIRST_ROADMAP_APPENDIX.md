# Syntavra Master Roadmap — Python-First Appendix

Status checkpoint: **2026-08-18**

## Current authority — 2026-09-06

This appendix is append-only historical roadmap material. Any earlier sequence or sentence in this file that implies `PYTHON_COMPLETE` directly lifts the Rust feature freeze or authorizes Remaining-71 feature/parity work is superseded by the later Rust retirement override and the canonical Python authority contract.

Current admitted state:

- `PYTHON_COMPLETE = true`
- `rust_resume_allowed = false`
- `rust_retired = true`
- Rust production promotion remains `174/245`, with `71` remaining.
- Rust feature/parity work requires a separate explicit reviewed/admitted reactivation authority.
- Production promotion remains a separate gate even after any future reactivation.

The historical execution diagrams and 2026-08-26 phase-exit wording below are retained for provenance only; they are not current reactivation authority.

This appendix extends the existing master roadmap. It does not delete, replace or invalidate previous roadmap material.

## Non-destructive roadmap rule

The master roadmap is append-only:

- Existing goals stay.
- Existing numbered capabilities stay.
- Existing Python/Rust architecture stays.
- New decisions and capabilities are appended.
- A stronger v2 capability does not mean the old primitive never existed; first classify it as EXISTS / HARDEN / UNIFY / NEW / CERTIFY / EXTERNAL.

## New execution order

Syntavra remains a Python + Rust project, but feature development is now staged:

```text
MASTER ROADMAP
      ↓
PYTHON PRODUCT / REFERENCE AUTHORITY
      ↓
COMPLETE NEW PRODUCT CAPABILITIES IN PYTHON
      ↓
PYTHON EXACT BEHAVIOR FREEZE
      ↓
PYTHON COMPLETION CERTIFICATE
      ↓
LIFT RUST FEATURE FREEZE
      ↓
PYTHON → RUST DIFFERENTIAL PORT
      ↓
ATOMIC NATIVE PROMOTION
      ↓
POST-PROMOTION CERTIFICATION
```

## Rust freeze boundary

Current migration baseline remains:

```text
Python public reference routes = 245
Rust promoted native          = 174
Remaining                     = 71
Remaining owned               = 71
Unowned                       = 0
Atomic target                 = 245
```

Until Python COMPLETE:

- No new Remaining-71 production promotion.
- No native promoted-counter change.
- No new Rust product feature implementation.
- No Python authority removal.
- No 174→245 promotion.
- Existing Rust code/contracts/tests are retained.
- Only build/security/data-loss/contract-blocking minimal Rust maintenance is allowed.

## Python-first product target

Python should become the complete product behavior authority for the intended Context OS / Universal Agent Runtime Control Plane:

```text
TASK
 ↓
INTENT
 ↓
TASK STATE
 ↓
CAPABILITY NEGOTIATION
 ↓
CONTEXT CANDIDATES
 ↓
TRUST / TAINT / FRESHNESS / RISK
 ↓
POLICY DECISION
 ↓
RETRIEVAL + TOOL DISCOVERY
 ↓
PROGRAMMATIC EXECUTION
 ↓
EVIDENCE STORE / TYPED OBJECTS
 ↓
BOUNDED / STRUCTURAL / SEMANTIC / EXACT VIEW
 ↓
MODEL
 ↓
VERIFICATION
 ↓
SAFE ACTION GATE
 ↓
OUTCOME
 ↓
USAGE + COST + LATENCY + QUALITY RECEIPT
 ↓
POLICY LEARNING
```

## Existing implementation must be reused

The current repository already has substantial Python foundations: command compactors/exact recovery, terminal spooling/streaming, prompt-cache planning, SQLite StructuralIndex, AST/tree-sitter/fallback repository intelligence, graph/PageRank analyses, provider routing, memory, secret redaction/security gates, compact MCP evidence, project-aware verifiers, bounded agent tool loops, exact structured edits, delivery modes, SDK/product surfaces and benchmark/receipt gates.

The Python-first phase therefore means **HARDEN + UNIFY + ADD MISSING CAPABILITIES + CERTIFY**, not “rewrite Syntavra from zero.”

## Python COMPLETE definition

Python COMPLETE requires all required capabilities to be implemented or intentionally certified as existing, deterministic replay/exact recovery, security gates, clean-install/fresh-repo smoke, Windows/Linux basic runtime validation, SignalBench product validation, frozen behavior/contracts, exact-head certification and a machine-readable Python Completion Certificate.

Python COMPLETE does not manufacture external superiority/adoption/maturity claims; real external evidence remains externally gated.

# New Capabilities 236–280

These continue the previous roadmap numbering.

## 236. Python Authority Mode
Machine-readable declaration that Python is the canonical product behavior authority during Python-first development.

## 237. Rust Feature Freeze Guard
CI policy preventing Remaining-71 promotion, native-counter movement and new Rust product features before Python COMPLETE.

## 238. Capability Completeness Registry
Track every roadmap capability as `planned`, `partial`, `implemented`, `verified`, or `certified`, including supporting tests and artifacts.

## 239. Python Completion Certificate
Machine-readable proof that required Python completeness, security, verification, benchmark and clean-install gates passed.

## 240. Runtime Contract Version Graph
Dependency graph across capability-contract/schema versions so downstream invalidation is explicit.

## 241. Context Decision Trace
Deterministic trace of include/omit/compress/retrieve/reset/abstain decisions.

## 242. Deterministic Policy Snapshot
Hash/snapshot the exact policy used for a task and attach it to receipts/replay.

## 243. Evidence Mutation Journal
Append-only evidence lifecycle: ingest → normalize → derive → compress → supersede → revoke.

## 244. Recovery Handle Integrity Proof
Verify every recovery handle resolves to the exact hashed artifact and valid object/range boundaries.

## 245. Task-Local Context Transaction
Treat task context mutations transactionally, with commit/rollback of failed optimization decisions.

## 246. Context Delta Compiler
Produce previous-view → next-view semantic/exact deltas rather than resending full context every turn.

## 247. Context Budget Explanation Plan
Explain allocation across repo/tool/memory/system/history/evidence and attach the plan to receipts.

## 248. Policy Conflict Resolver
Deterministic precedence and conflict receipts for global/repo/task/model/security policy conflicts.

## 249. Minimum Evidence Schema
Define required evidence for each task/action class; missing critical evidence triggers VERIFY or ABSTAIN.

## 250. Source-Specific Trust Calibration
Separate trust calibration for local files, git, user input, MCP, web, generated summaries, memory and remote APIs.

## 251. Cache Invalidation Provenance
Invalidate cache by source hash, commit, dependency, tool version and policy version, not TTL alone.

## 252. Context Leak Detector
Detect secrets/PII/unrelated-project evidence crossing task, project or agent boundaries.

## 253. Compression Safety Classes
Classify content as `EXACT_ONLY`, `STRUCTURAL_SAFE`, `SEMANTIC_SAFE`, or `LOSSY_ALLOWED`.

## 254. Semantic Preservation Verifier
Verify summaries/compression preserve critical entities, errors, constraints, numbers, paths, permissions and negations.

## 255. Cross-Host Adapter Conformance Suite
Run canonical tasks across Codex/Claude/Cursor/OpenCode/etc. adapters and verify equivalent Syntavra semantics.

## 256. Action Dry-Run Simulator
Where possible, simulate filesystem/git/network/package/deploy side effects before critical actions.

## 257. Fault Injection Harness
Inject timeout, malformed JSON, partial output, stale file, missing dependency, revoked permission and network failure scenarios.

## 258. Golden Corpus Generator
Generate permitted/anonymized deterministic golden context/evidence fixtures from real tasks.

## 259. Live Task Replay Fixture
Turn successful/failed task receipts into local replayable fixtures.

## 260. Reproducibility Capsule
Bundle repo commit, policy hash, provider profile, tool versions, fixtures, verifier and environment metadata.

## 261. Performance Budget Gate
Require correctness plus CPU/RAM/disk/latency/token-overhead budgets.

## 262. Memory Correctness Suite
Measure memory precision, recall, freshness, supersession, conflict, poisoning and wrong-project retrieval.

## 263. Tool Schema Compatibility Fingerprint
Tool/MCP schema changes invalidate affected policies, caches and tool-selection fixtures.

## 264. Tool Discovery Degradation Mode
Fallback to deterministic namespace/keyword discovery when semantic discovery is unavailable; ambiguous cases fail closed.

## 265. Provider Capability Negotiation
Negotiate provider/host capabilities at runtime and emit explicit unsupported/fallback receipts.

## 266. Prompt Cache Stability Guard
Detect unnecessary cache bust from context reordering or metadata drift and attribute the cause.

## 267. Multi-Agent Handoff Contract Verifier
Verify handoff evidence handles, state, constraints and completed work before another agent consumes them.

## 268. Secret-Aware Artifact Store
Separate encryption/redaction/retention/reveal policy for evidence containing secrets or credentials.

## 269. Evidence Retention + Garbage Collection Policy
Bound content-addressed storage growth without breaking provenance chains.

## 270. Evidence Hash Chain
Tamper-evident hash chaining for critical task/action evidence.

## 271. Python-to-Rust Contract Export
After Python COMPLETE, export canonical schemas, golden fixtures, error/exit contracts and behavior vectors for Rust.

## 272. Python-to-Rust Differential Snapshot
Frozen Python input/output/state/receipt corpus used as Rust parity authority.

## 273. Rust Resume Gate
Rust feature freeze may lift only when Python Completion Certificate = PASS and frozen differential corpus exists.

## 274. Atomic Rust Promotion Planner
Develop parity incrementally, but switch production authority only when every required family passes.

## 275. Post-Promotion Python Oracle
Keep frozen Python as a differential oracle for a defined certification window after 245/245 Rust promotion.

## 276. Feature Surface Budget
A new capability must not automatically create a new public command; measure public-surface complexity.

## 277. Internal Capability Composition
Compose capability graphs behind stable `context/evidence/search/execute/verify/memory` primitives.

## 278. Product Profile Certification
Separate certification for minimal, balanced, audit and future adaptive profiles.

## 279. No-Silent-Fallback Receipt
Any fallback must record cause, risk change and selected path instead of silently degrading.

## 280. Context Quality SLO Gate
Release stops if token savings violate minimum task-success, critical-evidence, verifier-success or unsafe-action SLOs.

# First Python Implementation Order

After PR #132 is cleanly merged:

```text
1. python_authority_v1
2. capability_completeness_registry_v1
3. rust_feature_freeze_guard_v1
4. universal_context_item_v1
5. evidence_store_v2
6. typed_context_object_store_v1
7. programmatic_execution_v1
8. deferred_tool_discovery_v1
9. unified_context_namespace_v1
10. adaptive_context_policy_v1
```

Do not start item N+1 until item N has acceptance tests and exact-head evidence.

# Rust Resume Order

```text
Python COMPLETE
→ frozen contract/behavior corpus
→ lift Rust freeze
→ rebase Remaining-71 parity on frozen Python
→ port new capabilities
→ final family certification
→ atomic 174→245 production promotion
→ 245/245 post-promotion certification
→ Python oracle window
```

## 2026-08-26 Python Completion phase-exit admission

- `python_completion_certificate_v1` advanced from `partial` to `certified` only after the dedicated implementation pass was merged and exact-head Linux/Windows clean-install receipts were available.
- `PYTHON_COMPLETE = true` now means every required internal Python capability is certified; external superiority, adoption and marketplace maturity remain outside this repository-internal claim.
- Rust feature development and Remaining-71 parity work may resume after Python COMPLETE.
- Rust production promotion remains a separate authority boundary at **174/245 promoted, 71 remaining**. Python COMPLETE does not mutate the production promotion counter and does not itself grant 174→245 promotion.
- The Rust freeze guard remains active for production-promotion authority and native promotion-counter changes while allowing post-completion feature/parity work.

## 2026-08-26 Rust retirement override

Python Completion Certificate may be PASS while Rust remains explicitly retired/frozen. `PYTHON_COMPLETE` is necessary but not sufficient for Rust reactivation. Until a separately admitted reactivation decision exists, `rust_resume_allowed=false`, native/Remaining-71 feature work stays closed, and the production baseline remains 174/245 with 71 remaining. Current active development continues on Python additions, hardening, fixes and certification.


## 2026-08-26 Python continuation while Rust is retired

The Rust retirement override does not end Python product development. While `rust_resume_allowed=false`, capabilities **240–270** continue as Python-side runtime/product hardening and additions in dependency order, capabilities **271–275** remain deferred because they are Rust-transition work, and capabilities **276–280** remain Python product-quality/composition work subject to their Python prerequisites. Python COMPLETE and Rust reactivation are separate state machines; no Python capability implicitly authorizes Rust feature/parity work or production promotion.
