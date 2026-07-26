# ADR 0008: Read-only state-root inspection parity

## Status

Proposed for R8.

## Context

R7 established shared state-layout metadata and a project-bound receipt envelope, but the Rust engine still did not read any real project state. Jumping directly to shared SQLite access would combine path confinement, symlink handling, state discovery, schema compatibility, database open modes, logical-row normalization, and mutation safety in one step.

## Decision

R8 introduces one deliberately narrow capability: `state.inspect`.

Both engines inspect the same project root and return a canonical inventory for the known `.syntavra` paths. The inspection is metadata-only:

- the project root is canonicalized and bound to a SHA-256 project identifier;
- a caller-supplied expected project identifier must match;
- symlinks in the project state path are rejected fail-closed;
- only contract-declared paths are inspected;
- regular files are bounded before reading and receive a SHA-256 digest;
- directories are reported without recursive traversal;
- missing paths are represented explicitly;
- unsupported file types fail closed;
- no directory, file, lock, journal, or database is created or modified.

R8 does not open SQLite. Database files, when eventually declared by a later contract, remain opaque files at this phase.

## Consequences

R8 proves real filesystem state reads and cross-engine metadata parity while preserving Python as the reference and default engine. It does not prove logical database compatibility, migrations, writes, locking interoperability, MCP parity, process parity, or recovery.

The next state phase may add read-only logical SQLite snapshots only after this path and mutation boundary remains green across supported platforms.
