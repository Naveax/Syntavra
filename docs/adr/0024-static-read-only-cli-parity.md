# ADR 0024: Static read-only CLI parity

## Status

Accepted for the first R24 production slice.

## Context

Python exposes `pipeline describe` and `plugins list` as deterministic introspection commands. The previous Python CLI initialized `EvidenceStore` before dispatch, so these nominally read-only commands could create key, directory and SQLite state. Rust had no corresponding commands.

## Decision

Both commands are defined by `contracts/cli/read-only-static-v1.json` and implemented natively in Python and Rust.

- `pipeline describe` returns the canonical runtime stage description.
- `plugins list` returns the empty explicit-only registry inventory.
- Python dispatches both before EvidenceStore, configuration, observability or plugin-registry construction.
- Rust embeds and parses the shared contract without invoking Python.
- Installed direct CLI output remains the command result object.
- Engine routing uses R24 schema version 12 and the existing route-scoped capability-aware auto policy.
- Rust candidate output must equal the Python canonical object exactly.
- Candidate errors and parity drift fail closed without Python re-execution.

## Safety boundary

These commands perform no filesystem, database, network, process or product-state mutation. They accept no route input. Output is bounded by the existing 1 MiB read-only response limit.

## Consequences

R24 proves two additional public CLI commands but does not complete the full read-only CLI surface. `cli.read-only.complete` remains `PYTHON_ONLY` until every remaining read-only command is independently contracted and verified.
