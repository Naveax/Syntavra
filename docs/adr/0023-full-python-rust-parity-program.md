# ADR 0023: Full Python–Rust parity program

- Status: Accepted
- Date: 2026-07-29
- Scope: R23–R37

## Context

R0–R22 proved a bounded native Rust read-only route surface. The Python engine still owns most product behavior: mutation, process execution, MCP, setup, provider routing, intelligence, benchmarks and publication.

Continuing feature-by-feature without a complete inventory risks silent Python-only growth and false parity claims.

## Decision

Adopt `contracts/parity/python-rust-full-parity-v1.json` as the canonical parity catalog.

All remaining workstreams R23–R37 start together as an explicit program. They are implemented through dependency-ordered, reviewable PRs rather than one monolithic change.

A new Python public surface must be represented in the catalog. `PARITY_PROVEN` requires a Rust owner, versioned contract and real parity verifier. `RUST_PRODUCTION_READY` additionally requires the complete platform/package and failure matrix.

Python remains the reference and default engine until R37. Rust may not invoke Python for features claimed as native. Hidden fallback remains forbidden.

## Consequences

- Progress is measured by explicit feature and dimension status, not a single averaged percentage.
- CI fails on catalog drift, missing proven capabilities, premature full-parity claims or version/channel drift.
- Mutation, MCP and process authority remain blocked until their dedicated workstreams pass.
- Rust-only distribution cannot be certified until Python is absent from the test environment.
- The catalog and exported surfaces become permanent release evidence.
