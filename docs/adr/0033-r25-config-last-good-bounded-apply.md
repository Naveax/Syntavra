# ADR 0033: R25 config last-good bounded apply transaction

## Status

Accepted for the second R25 shadow-parity slice.

## Context

R25 first introduced a deterministic, no-write plan for the durable last-good configuration. That plan proves project binding, persistent-scope filtering, payload identity and the `write` versus `retain-existing` decision, but deliberately leaves filesystem authority blocked.

The existing Python runtime writes `config-last-good.json` through a generic temporary-file replacement helper. It does not expose a language-independent lock contract, does not model stale-lock recovery, and historically allowed snapshots containing session or task overrides to reach the persistence boundary. Rust has no equivalent mutation path.

Granting public Rust mutation authority at this point would be premature. The transaction must first be independently executable and differentially tested across Windows, Linux and macOS while public routing remains blocked.

## Decision

Introduce `syntavra-config-last-good-apply-v1` as a bounded shadow transaction implemented independently in Python and Rust.

The transaction:

- reuses the proven R25 lifecycle plan and refuses any plan/input mismatch;
- accepts only project-bound persistent-scope `R6CFG1` input;
- fixes the target to `.syntavra/pre-release/config-last-good.json`;
- uses a project-bound exclusive lock at `.syntavra/pre-release/config-last-good.lock`;
- treats a lock as stale after 300 seconds and removes it only when its binding is valid;
- uses one lock-protected temporary path inside the target directory;
- flushes and synchronizes the temporary file before replacement;
- performs atomic replacement and best-effort directory synchronization;
- applies mode `0600` where the platform supports POSIX modes;
- verifies the written snapshot by reading it back and checking the candidate digest;
- promotes a matching synchronized temporary file when recovering from a crash before replacement;
- recognizes an already-current target without rewriting it;
- retains an existing target only when its `config_hash` matches the fallback candidate;
- accepts the legacy `loaded_at` field while retaining an existing Python snapshot;
- emits a deterministic receipt without absolute paths, raw configuration wire or secret material.

The transaction exposes explicit fault points after lock acquisition, temporary-file synchronization and target replacement. Fault injection deliberately preserves lock/temp state to model process termination; the next invocation must recover through the same contract.

## Authority boundary

This slice grants only `bounded-shadow` filesystem authority to the dedicated Python entry point and `syntavra-config-lifecycle-apply` Rust binary.

It does not authorize:

- installation into the public CLI router;
- `auto` engine selection;
- MCP mutation;
- setup, repair or host mutation;
- state/receipt writes outside this one target;
- profile lifecycle mutation;
- R26 state mutation work;
- a production-ready or full-parity claim.

Python remains the reference/default engine. Rust remains experimental. Product identity remains `0.0.1` pre-release and `FULL_PARITY_NOT_PROVEN` remains unchanged.

## Verification

The permanent R25 apply gate must run on Windows, Linux and macOS and cover:

- initial write;
- idempotent already-current behavior;
- replacement of an older valid snapshot;
- legacy-reader-compatible retain behavior;
- live-lock rejection;
- stale-lock recovery;
- crash after temporary-file synchronization;
- crash after target replacement;
- exact Python/Rust receipt bytes;
- exact post-transaction file bytes;
- no hidden Python invocation by Rust.

## Follow-up

R25 remains incomplete. The next slice must integrate the proven transaction into the canonical config/profile lifecycle surface, remove the legacy ephemeral-scope persistence path, and prove profile create/select/update/delete behavior before R26 may begin.
