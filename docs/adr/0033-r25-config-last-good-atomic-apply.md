# ADR 0033 — R25 Config Last-Good Atomic Apply

## Status

Accepted for experimental shadow parity. Public mutation routing remains blocked.

## Context

R25 first established a deterministic no-write lifecycle plan for `.syntavra/pre-release/config-last-good.json`. The Python reference runtime already persists last-good configuration, but broad Rust write authority cannot be inferred from planning parity. A narrowly scoped mutation primitive is required before config/profile lifecycle parity can advance.

## Decision

Add a project-bound Python reference apply primitive and a native Rust shadow primitive with the following fixed boundary:

- the only target is `.syntavra/pre-release/config-last-good.json`;
- the only lock is `.syntavra/pre-release/config-last-good.lock`;
- session and task scopes are rejected by the existing lifecycle plan;
- `retain-existing` performs zero filesystem mutation;
- `write` uses an exclusive lock, a same-directory temporary file, file sync, mode `0600`, atomic replacement and directory sync where supported;
- equal existing bytes produce `unchanged` without target replacement;
- project-root, parent, target and lock symlinks fail closed;
- owned temporary and lock files are removed on every exit path;
- results expose hashes and mutation booleans, never absolute paths, raw config wire, raw payload or temporary names;
- Python and Rust must produce byte-identical final payloads and canonical result objects in real Cargo-backed fixtures.

## Explicit exclusions

This decision does not expose an installed CLI mutation route and does not grant:

- general state or receipt writes;
- stale-lock recovery;
- config migration or repair;
- profile installation;
- SQLite mutation;
- MCP mutation;
- process execution;
- setup or host changes.

Python remains the reference/default engine. Rust remains experimental. Version remains `0.0.1` pre-release and `FULL_PARITY_NOT_PROVEN` remains unchanged.

## Consequences

R25 gains a real but isolated write primitive that can be fault-tested before public routing. Later R25 slices may add guarded installed routing only after exact-head Windows, Linux and macOS parity remains green and catalog assembly is complete.
