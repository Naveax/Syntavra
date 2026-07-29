# ADR 0021: Installed bounded live broker snapshot routing

## Status

Accepted for R21.

## Context

R10 proves Python/Rust parity for bounded SQLite online backups of quiescent and WAL-backed broker databases. R20 admits only the quiescent immutable snapshot route. Direct Rust capability availability does not authorize installed live database routing.

A live route must preserve project binding, reject unsafe path and sidecar states, bound backup work, avoid source writes, prevent hidden fallback, and account for the fact that two sequential online backups can observe different source-change metadata even when their stable logical snapshots agree.

## Decision

Add `state.broker-live-snapshot` to the installed read-only route whitelist.

The installed CLI accepts:

```text
--database-path <project-bound-broker.sqlite3>
```

The route:

1. validates the lexical project root and derives its project ID before engine selection;
2. requires a project-contained, symlink-free `broker.sqlite3` path;
3. rejects rollback journals, sidecar symlinks, and incomplete WAL/SHM pairs;
4. opens the source read-only with `query_only=ON` and `trusted_schema=OFF`;
5. uses SQLite online backup into memory only;
6. bounds the logical database to 64 MiB, the backup to 5 seconds, and each step to 64 pages;
7. validates the exact broker schema, indexes, foreign keys, logical row types, and project binding on the completed backup;
8. performs no checkpoint, vacuum, migration, destination-file creation, or persistent state write;
9. executes the Rust candidate only after Python reference preflight succeeds;
10. validates the Rust candidate's canonical `snapshot_hash`;
11. compares the stable complete result after excluding only `snapshot_hash` and the observational `database.source_changed_during_backup` field;
12. rejects logical cross-engine source drift with mismatched top-level keys and SHA-256 digests only;
13. returns the Python reference snapshot after the selected Rust candidate proves the stable projection;
14. never retries or re-executes after Rust failure.

Returning the Python reference result is not fallback. Python preflight is mandatory for both engine selections and occurs before candidate execution. A Rust failure terminates the route.

## Consequences

The installed read-only router now admits:

```text
config.resolve
receipt.inspect
state.broker-live-snapshot
state.broker-snapshot
state.inspect
state.layout
status
version
```

Python remains the reference and default engine. `auto` remains Python. Broker writes, migrations, recovery, MCP, process execution, installer mutation, and all Rust write authority remain outside the installed boundary.
