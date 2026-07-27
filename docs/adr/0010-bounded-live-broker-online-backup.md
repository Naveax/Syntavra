# ADR 0010: Bounded live broker snapshots through SQLite online backup

## Status

Proposed for R10.

## Context

R9 proves logical Python/Rust parity for a quiescent `broker.sqlite3` database opened read-only and immutable. That boundary correctly rejects `-wal`, `-shm`, and `-journal` sidecars, but a running process broker normally uses WAL mode and can have concurrent readers and writers.

Copying the main database file, or copying the database and WAL as unrelated filesystem objects, does not provide a transactional snapshot. Checkpointing, vacuuming, or migrating the project database would violate the read boundary. A consistent live snapshot therefore requires SQLite's online backup protocol.

## Decision

R10 introduces `state broker-live-snapshot` for one live `broker.sqlite3` database.

The source connection is bounded and read-only:

- R8 project-root and project-identifier binding remains mandatory;
- the source database must remain beneath the canonical project root;
- the database and any declared sidecar must be a regular non-symlink file;
- the source opens with SQLite `mode=ro` and `PRAGMA query_only=ON`;
- the destination is an in-memory SQLite database, not a project or temporary filesystem file;
- the SQLite online backup API copies a bounded number of pages per step;
- busy and locked responses may retry only until a monotonic deadline;
- source page size, page count, and projected snapshot bytes are bounded before and during backup;
- cancellation by timeout, size growth, invalid schema, or SQLite error fails closed;
- the completed in-memory database is validated and canonicalized through the R9 logical broker contract;
- no checkpoint, migration, vacuum, transaction write, destination file, or project-state artifact is created by Syntavra.

The fixed R10 limits are contract data rather than caller-controlled claims:

- maximum logical database bytes: 64 MiB;
- pages per backup step: 64;
- maximum backup duration: 5 seconds;
- retry sleep: 10 milliseconds;
- maximum busy/locked retries: bounded by the same deadline.

The receipt records source journal mode, sidecar presence, copied-page progress, retry count, elapsed time, and whether the source changed while the consistent backup was being produced. Source changes by concurrent writers are observational data and do not invalidate a completed SQLite backup.

## Security boundary

The source SQLite connection is incapable of issuing writes because it is opened read-only and placed in query-only mode. R10 does not claim that SQLite coordination metadata is byte-stable while other processes are using the database; it claims that Syntavra does not issue a source write and that the logical result comes only from the in-memory backup destination.

## Consequences

R10 permits logical inspection of active WAL-mode broker state while writers continue. It remains limited to the broker schema version proven by R9.

R10 does not add database writes, migrations, checkpoint control, restore, rollback, recovery, incremental backup persistence, arbitrary SQLite schemas, or an unbounded export surface.
