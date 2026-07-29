# ADR 0022: Route-scoped capability-aware automatic engine selection

## Status

Accepted for R22 implementation.

## Context

R11-R21 proved eight installed read-only Python/Rust routes while preserving Python as the reference and default engine. Until R21, the `auto` preference always resolved to Python, even when a verified Rust binary exposed the exact route capability.

Changing the global default or automatically routing mutating/general commands would exceed the proven contract surface. Automatic Rust admission must therefore be scoped to one already-proven route and completed before route execution starts.

## Decision

R22 adds a router-local automatic selection policy:

- explicit `python` remains Python;
- explicit `rust` remains Rust and fail-closed;
- `auto` evaluates the normalized route before execution;
- Rust is selected only when the route is in the eight-route installed read-only whitelist;
- the current platform is `linux`, `win32` or `darwin`;
- the Rust binary is available;
- the complete product/version/channel/descriptor/capability contract is compatible;
- the exact route capability is present;
- otherwise Python is selected before either engine starts.

The eligible routes are:

- `version`
- `status`
- `config.resolve`
- `state.layout`
- `state.inspect`
- `receipt.inspect`
- `state.broker-snapshot`
- `state.broker-live-snapshot`

A pre-execution Python choice is policy resolution, not fallback. Once Rust route execution begins, any execution, schema, hash or parity failure terminates the request without Python re-execution.

## Result envelope

Auto-routed results preserve the original selection provenance and report:

- `requested=auto`;
- the resolved engine;
- policy `route-scoped-capability-aware-rust-v1`;
- route, platform and capability eligibility booleans;
- a stable reason code;
- `rust_started=false` at the decision point;
- `fallback_attempted=false`.

Executable paths, raw contract output and candidate exception messages are not added to the auto decision metadata.

## Deliberate exclusions

R22 does not enable automatic routing for:

- general product commands;
- MCP tools;
- process execution;
- database writes, migration or recovery;
- installer mutation;
- any capability not already proven read-only;
- Rust as the global default engine.

Version remains locked at `0.0.1`. Rust remains experimental and read-only.
