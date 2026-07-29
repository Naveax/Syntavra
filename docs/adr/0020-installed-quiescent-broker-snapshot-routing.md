# ADR 0020: Installed project-bound quiescent broker snapshot routing

## Status

Accepted for R20.

## Context

R9 proves exact Python/Rust logical-read parity for a closed `broker.sqlite3` database. The Rust binary already exposes `state broker-snapshot`, but direct capability availability does not authorize installed routing. R18 establishes lexical project-root validation and project-ID derivation at the installed boundary, while R19 establishes project-bound canonical input handling and no-fallback parity diagnostics.

A broker database may contain active WAL, SHM or rollback-journal state. Treating such a database as immutable would be unsafe. The first installed database route therefore admits only a quiescent database with no sidecars. Live backup semantics remain a separate phase.

## Decision

Add `state.broker-snapshot` to the installed read-only route whitelist.

The installed CLI accepts:

```text
--database-path <project-bound-broker.sqlite3>
```

The route:

1. requires the selected lexical `--project` root to be an existing non-symlink directory;
2. derives the expected project ID from the normalized canonical absolute path;
3. requires a database path inside the selected project root;
4. requires the final database name to be exactly `broker.sqlite3`;
5. rejects symlinked parents and a symlinked database file;
6. rejects `-journal`, `-shm` and `-wal` sidecars before engine selection;
7. opens the database in Python with `mode=ro`, `immutable=1` and `query_only=ON`;
8. validates the exact broker schema version, tables, columns, indexes and foreign keys;
9. validates logical row types, canonical JSON fields and project binding;
10. rejects a database that changes during the read;
11. invokes Rust with exactly `state broker-snapshot <project-id> <project-root> <database-path>` after successful Python preflight;
12. compares the complete Python and Rust result objects;
13. reports only mismatched top-level keys and result SHA-256 digests on parity drift;
14. redacts candidate execution messages;
15. never retries in Python after Rust starts.

Success input metadata contains only a canonical material byte count and SHA-256 digest derived from the project ID and canonical relative database path. The selected project-root path and absolute database path are forbidden in route envelopes and errors. The canonical result may expose the validated relative database path.

## Consequences

The installed read-only router now admits:

```text
config.resolve
receipt.inspect
state.broker-snapshot
state.inspect
state.layout
status
version
```

The route reads SQLite state but performs no filesystem mutation, database write, sidecar creation, migration or recovery. Python remains the reference and default engine. `auto` remains Python.

`state.broker-live-snapshot`, WAL backup, mutating broker operations, MCP, process execution, migrations, recovery, installer mutation and all write authority remain outside the installed Rust boundary and require separate parity phases.
