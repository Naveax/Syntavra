# ADR 0032: R25 config last-good lifecycle planning

## Status

Accepted for the first R25 shadow-parity slice.

## Context

The Python `ConfigManager` persists `.syntavra/pre-release/config-last-good.json` after a successful load and reuses it when the current configuration is invalid. Rust already resolves the canonical `R6CFG1` configuration wire and reproduces last-good fallback semantics in memory, but it has no filesystem mutation authority.

Moving directly to Rust writes would combine policy, project binding, payload canonicalization, atomic replacement and crash recovery in one high-risk change. The lifecycle must first be represented as a deterministic, no-write plan that both engines produce identically.

The current Python implementation can also persist snapshots containing session or task overrides. Those scopes are ephemeral and must not become durable last-good state. R25 therefore makes that unsafe legacy behavior explicit and rejects ephemeral scopes at the persistence-plan boundary. The later apply slice will update both engines together.

## Decision

Introduce `syntavra-config-last-good-plan-v1` as a shadow-only contract.

Inputs:

- expected project ID;
- symlink-safe project root;
- bounded canonical `R6CFG1` wire containing persistent scopes only.

The plan:

- binds the operation to the selected project root;
- fixes the target to `.syntavra/pre-release/config-last-good.json`;
- derives deterministic canonical payload size and SHA-256 digest;
- returns `write` for a valid current configuration;
- returns `retain-existing` when an invalid current phase falls back to a prior valid phase;
- rejects invalid input without a prior valid phase;
- rejects session and task scopes;
- excludes the absolute project path, absolute target path, raw wire, raw secret material and `loaded_at`;
- performs no filesystem or database mutation;
- keeps apply authority blocked.

Python and Rust must produce byte-identical canonical plan JSON. A dedicated Rust binary is used for shadow verification, but the command is not installed into the public router during this slice.

## Safety boundary

This ADR does not authorize:

- creation or replacement of `config-last-good.json`;
- lock acquisition or stale-lock recovery;
- temporary files, rename, fsync or directory sync;
- config migration or repair;
- public auto routing;
- MCP, process or setup mutation;
- any broader product-parity claim.

Python remains the reference/default engine. Rust remains experimental. Product identity remains `0.0.1` pre-release and `FULL_PARITY_NOT_PROVEN` remains unchanged.

## Follow-up

The next R25 slice will implement an apply transaction only after atomic-write fault injection, project-bound lock semantics, legacy-reader compatibility and crash-recovery tests pass on Windows, Linux and macOS.
