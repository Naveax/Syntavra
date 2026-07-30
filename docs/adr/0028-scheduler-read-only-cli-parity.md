# ADR 0028: Scheduler Read-Only CLI Parity

## Status

Accepted for the R24 parity program.

## Context

The public Python commands `scheduler stats` and `scheduler list` appeared read-only, but constructed `DurableJobScheduler`. Its `StateDB` constructor creates parent directories, initializes SQLite, enables WAL and creates scheduler tables and indexes. The unified CLI also initialized `EvidenceStore` before reaching scheduler handling.

Consequently, inspecting an empty project could create product state. A Rust implementation that copied only the visible query behavior would not provide safe behavioral parity.

## Decision

R24 introduces a separate quiescent scheduler inspection contract for:

- `scheduler.stats`
- `scheduler.list`

Both public engines use one Python canonical preflight and one native Rust candidate implementation.

The contract:

- derives the database only as `<selected-state-root>/scheduler.sqlite3`;
- returns deterministic empty results when the database is absent;
- creates no directory, database or SQLite sidecar;
- rejects state-root, database and sidecar symlinks where present;
- rejects rollback-journal, SHM and WAL sidecars;
- limits the source database to 64 MiB;
- opens SQLite read-only and enables `query_only=ON` and `trusted_schema=OFF`;
- validates exact scheduler tables, columns, indexes and foreign key shape;
- validates the JSON types stored in `argv_json`, `metadata_json` and `result_json`;
- canonicalizes state filters and clamps list limits to 1–1000;
- compares the complete Python and Rust result objects;
- rejects source identity changes observed during inspection;
- forbids Python re-execution after Rust starts.

The unified Python CLI intercepts these two actions before `EvidenceStore` or `DurableJobScheduler` construction.

## Consequences

Active WAL-backed scheduler inspection is not admitted by this ADR. A running scheduler database with `-wal` or `-shm` fails closed. A future bounded live-snapshot contract may add that capability separately.

`scheduler reap` remains mutating and Python-only. Scheduler submission, claim, heartbeat, completion, failure, cancellation, retry, migration and recovery remain outside R24.

Python remains the reference engine and product default. Rust remains experimental and read-only. Product identity remains `0.0.1`, `pre-release`, `pre-alpha`.

## Evidence

- `contracts/cli/scheduler-read-only-v1.json`
- `tests/runtime/test_scheduler_read_only_r24.py`
- `tools/verify_r24_scheduler_read_only.py`
- `.github/workflows/r24-scheduler-read-only.yml`
- aggregate R0–R24 parity
