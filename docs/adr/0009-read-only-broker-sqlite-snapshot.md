# ADR 0009: Read-only logical broker SQLite snapshots

## Status

Proposed for R9.

## Context

R8 proved that both engines can inspect contract-declared state paths without mutating project state. The next risk boundary is SQLite. Comparing database file hashes or page layouts would be incorrect because SQLite page allocation, freelists, journaling, and WAL checkpoint state are physical implementation details rather than product semantics.

The authoritative process-broker database already exposes an explicit schema version through `metadata.schema_version`. Schema version 2 contains four logical tables:

- `metadata`
- `jobs`
- `completion_events`
- `verifier_results`

## Decision

R9 introduces `state broker-snapshot` for one quiescent `broker.sqlite3` database.

The snapshot contract is deliberately read-only and fail-closed:

- the project root and expected project identifier use the R8 binding rules;
- the database path must resolve beneath the canonical project root;
- the database file and every path component must not be a symlink;
- `broker.sqlite3-wal` and `broker.sqlite3-shm` must not exist;
- the database opens through a read-only immutable SQLite URI;
- `PRAGMA query_only=ON` is required;
- `metadata.schema_version` must equal `2`;
- the exact declared table and column contract is verified;
- unknown user tables, missing tables, missing columns, extra columns, views, triggers, or user-defined indexes fail closed;
- rows are ordered by contract-defined primary keys;
- JSON text fields are parsed and emitted as canonical JSON values;
- SQLite integers, reals, text, and nulls are normalized to a shared typed JSON representation;
- BLOB values are not accepted by the R9 broker schema;
- no migration, checkpoint, vacuum, integrity repair, write transaction, lock file, journal, WAL, or SHM file is created.

R9 returns table row counts, canonical logical rows, and one SHA-256 digest over the canonical snapshot payload. Physical database bytes are intentionally excluded.

## Consequences

R9 proves cross-engine logical read parity for quiescent process-broker state. It does not prove live-WAL snapshots, concurrent-writer consistency, database writes, migrations, rollback, recovery, or compatibility with other Syntavra SQLite databases.

A later phase may add live snapshots through a separately bounded online-backup protocol after proving that no project-state sidecar can be created or changed.
