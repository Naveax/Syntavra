# ADR 0002: Python Remains the Reference Engine

- Status: Accepted
- Date: 2026-07-26

## Context

The current Python runtime defines the deployed CLI, MCP, state, evidence, process and installer behavior. Treating the first Rust implementation as co-authoritative would allow silent semantic drift.

## Decision

Python remains the behavioral reference until the Rust engine reaches the stable gate.

The reference surface is exported by `tools/export_python_contract.py`. Rust changes must compare against explicit contracts and parity fixtures rather than reinterpreting Python behavior informally.

## Consequences

- Existing users retain current behavior.
- Product fixes continue in Python while Rust parity work proceeds.
- Rust may intentionally improve internals, but observable contract changes require an explicit contract version change.
- A Rust-only behavior is experimental until adopted into the shared contract.
