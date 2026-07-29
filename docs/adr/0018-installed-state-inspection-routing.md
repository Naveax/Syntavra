# ADR 0018: Installed project-bound state inspection routing

## Status

Accepted for R18.

## Context

R8 proves bounded Python/Rust parity for inspecting a fixed set of `.syntavra` paths. R17 admits only the static state-layout description to the installed router. The next read-only state surface must preserve R8 project binding and no-follow filesystem rules without exposing the selected project path in installed route envelopes or diagnostics.

The engine selector stores a resolved project root for general runtime use. For R18, the installed CLI also passes the original lexical `--project` path to the router so a project-root symlink can be rejected before resolution erases that fact.

## Decision

Add `state.inspect` to the installed read-only route whitelist.

The route:

1. accepts the selected `--project` root and no configuration input;
2. rejects a missing, non-directory or symlink project root before engine selection;
3. derives the project identifier as SHA-256 of the normalized canonical absolute path;
4. performs the canonical Python `inspect_state_root()` preflight;
5. inspects only the contract-declared paths:
   - `.syntavra`
   - `.syntavra/config.toml`
   - `.syntavra/engine.json`
   - `.syntavra/pre-release`
   - `.syntavra/runtime-v3`
6. rejects symlinks and unsupported file types in every declared path component;
7. limits regular-file reads to 1 MiB and detects concurrent changes;
8. invokes Rust with exactly `state inspect <project-id> <project-root>` after successful preflight;
9. compares the complete Python and Rust result objects;
10. reports only mismatched top-level keys and SHA-256 result digests on drift;
11. redacts candidate execution messages and never falls back to Python after Rust starts.

The success input metadata contains only:

```text
profile = project-bound-state-root-v1
format = sha256-normalized-absolute-path-v1
bytes = 32
sha256 = <derived-project-id>
```

The selected project-root string is forbidden in success and error envelopes.

## Consequences

The installed router now admits:

```text
config.resolve
state.inspect
state.layout
status
version
```

R18 performs bounded filesystem reads but opens no database and mutates no state. Python remains the reference and default engine. `auto` remains Python. Receipt input, broker snapshots, MCP, process execution, migrations, recovery and all writes remain outside the installed Rust routing boundary.
