# ADR 0029 — Migration Plan Read-Only CLI Parity

## Status

Accepted for R24 pre-release parity work.

## Context

The Python `migrate plan` command previously instantiated `MigrationManager`. Its
`current_version()` path calls `_ensure_table()`, so an inspection command could
create `syntavra_schema_migrations` and mutate a database that had no migration
table. It could also create a missing SQLite database through the normal SQLite
open path.

R24 requires every read-only CLI route to have the same observable Python and
Rust behavior without creating state, directories, tables, journals, WAL files,
SHM files, backups, migration receipts or fallback executions.

## Decision

`migrate plan <database>` is implemented as the route `migration.plan` with a
separate Python/Rust contract.

The selected database path:

- must remain lexically and physically inside the selected project root;
- must be valid UTF-8 and at most 4096 bytes;
- rejects symlinked project roots, existing parent components and database files;
- rejects `-journal`, `-shm` and `-wal` sidecars;
- is opened read-only with `query_only=ON` and `trusted_schema=OFF`;
- is bounded to 64 MiB;
- is revalidated after inspection so concurrent source changes fail closed.

A missing database returns a deterministic zero-version plan and does not create
its parent directory. An existing database without
`syntavra_schema_migrations` also returns version zero without creating the
table. When the table exists, its exact columns and every row are validated
before the highest applied version is returned.

The installed route uses Python as the canonical preflight. If Rust is selected,
the verified `migration.plan` capability executes once and its complete result
must equal the Python result. A Rust failure or parity mismatch never causes a
Python re-execution.

## Canonical result

```json
{
  "database": "relative/path.sqlite3",
  "current_version": 0,
  "target_version": 0,
  "pending": []
}
```

R24 intentionally receives an empty migration registry, so `target_version`
equals `current_version` and `pending` is empty. Future migration-registry
parity requires a separate contract revision.

## Security properties

- no filesystem or database mutation;
- no absolute project or database path in the route result envelope;
- no hidden Python fallback after Rust starts;
- parity errors expose only mismatched top-level keys and SHA-256 digests;
- schema drift, malformed rows, sidecars, symlinks and source changes are
  rejected.

## Verification

The package includes:

- language-independent contract `migration-plan-read-only-v1.json`;
- Python unit and regression fixtures;
- a native Rust SQLite implementation;
- a Cargo-backed differential verifier;
- an independent R24 workflow;
- aggregate R0–R24 and Full Engine Parity integration.

## Consequences

`migrate plan` is now safe to run against an absent or existing project-bound
SQLite database. `migrate apply` and `migrate rollback` remain Python-owned,
mutating commands and are outside this parity claim.
