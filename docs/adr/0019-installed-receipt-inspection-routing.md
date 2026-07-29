# ADR 0019: Installed project-bound receipt inspection routing

## Status

Accepted for R19.

## Context

R7 proves byte-exact Python/Rust parsing parity for canonical `R7RCPT1` receipt envelopes. The Rust binary already exposes `receipt inspect`, but direct capability availability does not authorize installed routing. R18 establishes lexical project-root validation and project-ID derivation at the installed boundary.

Receipt routing must prevent cross-project replay, reject noncanonical transport, avoid exposing receipt contents or project paths, and remain independent of product-state files and databases.

## Decision

Add `receipt.inspect` to the installed read-only route whitelist.

The installed CLI accepts:

```text
--receipt-wire-hex <lowercase-hex>
```

The route:

1. requires the selected lexical `--project` root to be an existing non-symlink directory;
2. derives the expected project ID from the normalized canonical absolute path;
3. accepts at most 65,536 decoded receipt bytes;
4. requires non-empty, even-length lowercase hexadecimal transport;
5. decodes and validates the complete `R7RCPT1` receipt in Python before engine selection;
6. rejects unknown schema, field order, engine, identifier, hash, fallback and project-binding violations;
7. rejects receipts bound to another project;
8. invokes Rust with exactly `receipt inspect <project-id> <receipt-wire-hex>` after successful preflight;
9. compares the complete Python and Rust result objects;
10. reports only mismatched top-level keys and result SHA-256 digests on parity drift;
11. redacts candidate execution messages;
12. never retries in Python after Rust starts.

Success input metadata contains only the decoded byte count and SHA-256 digest. The raw receipt wire, receipt field values and selected project-root path are forbidden in route envelopes and errors.

## Consequences

The installed read-only router now admits:

```text
config.resolve
receipt.inspect
state.inspect
state.layout
status
version
```

The route performs no product-state file read, database access or mutation. Python remains the reference and default engine. `auto` remains Python. Broker snapshots, MCP, process execution, migrations, recovery, installer mutation and all write authority remain outside the installed Rust boundary.
