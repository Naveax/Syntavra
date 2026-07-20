# SignalCore Threat Model

The detailed runtime model is maintained in `docs/security/THREAT_MODEL.md`.

## Protected assets

- project source, repository identity and verifier state;
- exact stdout/stderr evidence and recovery handles;
- process arguments, environment boundaries and child-process lifetime;
- project/user-scoped memory and immutable session history;
- hook, MCP and host-capability negotiation;
- benchmark workload identity, quota telemetry and claim receipts;
- Roblox Studio activation, pairing material and capability authorization.

## Principal threats

- false claims of hook or runtime enforcement;
- destructive-command execution or cwd escape;
- secret leakage through tool output, memory or evidence metadata;
- orphaned or cross-project process workers;
- stale verifier reuse after tree, dependency, environment or toolchain changes;
- cross-project memory/evidence contamination;
- malformed, rotated or duplicated rollout events;
- summary loss, context-role eviction or non-recoverable compaction;
- benchmark gaming, unequal work and fabricated quota measurements;
- encoded/generated source hidden from review;
- signed-envelope replay or Roblox capability escalation.

## Controls

- truthful host negotiation with explicit degraded modes;
- argv-only execution, repository containment and destructive-pattern blocking;
- durable SQLite/WAL job state, project-scoped workers and completion cursors;
- bounded single-pass output filtering with secret redaction and exact SHA-256 evidence;
- content-, environment-, dependency- and toolchain-bound verifier identities;
- scoped FTS5 memory, supersession and relation provenance;
- immutable hash chains and exact recursive summary-DAG expansion;
- mandatory context roles and fail-closed budget packing;
- observed paired benchmark gates requiring equal work and actual quota telemetry;
- package validation rejecting payload transports and source-materializer workflows;
- strict signed Roblox activation with single-use nonces and transitive authorization.

## Residual risks

SignalCore does not defend against a fully privileged local operating-system attacker.
Live provider behavior, native host hook semantics, Roblox Studio transport, Creator Store,
Blender, device simulation and DataStore migration require separate live certification.
Internal tests and implementation benchmarks cannot inherit independent or public maturity.
