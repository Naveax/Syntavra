# ADR 0031: R24 read-only CLI completion certification

## Status

Accepted for implementation on the R24 completion branch.

## Context

R24 delivered native Python/Rust/auto parity in bounded slices. A list of successful pull requests is not sufficient evidence that the complete intended read-only CLI surface remains covered. New commands, catalog drift or a removed Rust capability could otherwise leave R24 partially implemented while individual regression workflows still pass.

## Decision

Introduce one language-independent completion contract and one fail-closed certification gate.

The contract defines the exact R22 and R24 read-only route inventory, the corresponding Python command paths, Rust capabilities and the underlying real-binary parity verifiers. The completion verifier requires:

- exact catalog inventory equality for the certified scope;
- `PARITY_PROVEN` status for every certified route;
- an existing Rust owner, contract and parity verifier for every route;
- complete Rust capability coverage;
- Python CLI exporter coverage for every public command path;
- the permanent `cli.read-only.complete` catalog feature;
- execution of every underlying Cargo-backed verifier in the dedicated CI gate.

The aggregate R0-R24 verifier consumes the structural completion result. The dedicated completion workflow additionally executes all real-binary verifiers.

## Certified scope

The certificate covers 17 read-only routes:

- version and status;
- config resolve, validate, explain and show;
- state layout and state inspection;
- receipt inspection;
- quiescent and live broker snapshots;
- pipeline description and plugin listing;
- scheduler stats and listing;
- migration planning;
- telemetry metrics.

## Exclusions

This decision does not grant or claim:

- state or receipt mutation;
- SQLite mutation, migration application or recovery;
- process execution;
- MCP authority;
- setup, installation or host mutation;
- provider, benchmark or publication parity;
- Rust standalone production readiness;
- complete Python/Rust product parity.

Python remains the reference and global default. Rust remains an experimental candidate. Product identity remains locked to `0.0.1` and `pre-release`.

## Consequences

R24 can be marked complete only when the exact route inventory, catalog evidence, exported Python surface, Rust capability surface and all real-binary parity verifiers pass on the same final commit. Any future read-only CLI addition must update the completion contract and provide equivalent Rust evidence or the gate fails closed.
