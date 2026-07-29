# ADR 0017: Installed state.layout routing

## Status

Accepted for R17.

## Context

R7 proves exact Python/Rust parity for the static Syntavra state-layout contract. The Rust binary already exposes `state layout`, but direct capability availability does not authorize installed routing. R11–R16 establish a capability-whitelisted, bounded and fail-closed installed router.

The next admissible state surface should not require filesystem, database or project-state reads. The static layout contract is therefore the lowest-risk state capability to admit before project-bound state inspection and receipt parsing.

## Decision

Add `state.layout` to the installed read-only route whitelist.

The route:

- accepts no input;
- invokes `state_layout()` in the Python reference engine;
- invokes `syntavra-rs state layout` in the Rust candidate engine;
- compares the complete result object exactly;
- validates the exact top-level schema before parity comparison;
- limits the serialized response to 1 MiB;
- reports only mismatched top-level keys and result SHA-256 digests on parity failure;
- redacts candidate execution errors;
- never re-executes in Python after Rust selection;
- creates, reads or mutates no `.syntavra` path;
- opens no SQLite database.

Configuration wires, live discovery and session/task overrides are rejected before engine selection.

## Consequences

The installed router now admits four read-only routes:

```text
config.resolve
state.layout
status
version
```

Rust remains experimental. Python remains the reference and default engine. `auto` remains Python. R17 grants no filesystem state-read authority, receipt input handling, database access, migration, recovery, process, MCP, installer or mutation authority.
