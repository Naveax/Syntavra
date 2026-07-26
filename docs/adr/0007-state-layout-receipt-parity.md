# ADR 0007: Read-only state layout and receipt envelope parity

## Status

Accepted for R7.

## Context

R6 proved deterministic configuration, identity, and status parity, but the Rust engine still had no shared contract for state layout metadata or durable receipt envelopes. Allowing either engine to interpret state without a versioned, project-bound, fail-closed envelope would create cross-project replay and silent schema-drift risks.

## Decision

R7 introduces two read-only Rust capabilities:

- `state.layout`
- `receipt.inspect`

The state-layout contract is versioned and explicitly marks Rust filesystem and database access as unproven. The Rust command returns contract metadata only; it does not open `.syntavra`, SQLite databases, lock files, or product state.

Receipts use the versioned `R7RCPT1` wire format. Field order is canonical. Text-bearing values are UTF-8 encoded as lowercase hexadecimal. `receipt_hash` is SHA-256 over the canonical wire through `fallback_state_mutated`, including the trailing newline and excluding the `receipt_hash` line.

Every receipt is bound to a 64-character lowercase SHA-256 `project_id`. The caller supplies the expected project identifier, and any mismatch fails closed. Unknown fields, order changes, schema versions, product versions, contract versions, engines, malformed hashes, invalid identifiers, and post-mutation fallback claims fail closed.

Fallback receipts are valid only when:

- `from` and `to` are distinct supported engines;
- the reason is non-empty;
- `state_mutated` is `false`;
- the receipt engine equals the fallback target.

## Consequences

Python remains the reference and default engine. R7 proves only static state-layout metadata and receipt-envelope parsing. It does not prove filesystem state reads, shared-state writes, database compatibility, migrations, MCP behavior, process execution, or recovery.

The next state phase must build on these identifiers and envelopes rather than inventing engine-specific formats.
