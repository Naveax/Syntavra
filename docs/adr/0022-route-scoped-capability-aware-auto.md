# ADR 0022 — Route-Scoped Capability-Aware Auto Selection

## Status

Accepted for R22 implementation.

## Context

R11–R21 proved eight installed read-only Python/Rust routes while preserving
Python as the reference and default engine. Until R22, requesting `auto` still
resolved globally to Python even when the selected installed route, platform,
binary and Rust contract had already passed parity gates.

A global Rust default remains unsafe because mutating commands, MCP, process
execution, migration and recovery are not proven. The automatic decision must
therefore be scoped to one already-admitted route and must occur at the route's
existing post-preflight boundary.

## Decision

R22 introduces `route-scoped-capability-aware-r22`.

`auto` selects Rust only when all of the following are true:

1. The command is one of the eight installed read-only routes admitted through
   R21.
2. The route's Python-owned input and filesystem/database preflight has already
   completed successfully.
3. The current platform pair is admitted by the R22 contract.
4. A Rust binary is present.
5. Product identity, version `0.0.1`, pre-release channel, contract hash and the
   complete capability surface verify successfully.
6. The selected route's required capability is present.

If any selection-time check fails, `auto` selects Python before Rust candidate
execution. This is a routing decision, not fallback.

After Rust has been selected, candidate execution, schema, hash or parity
failure terminates fail-closed. Python is not re-executed.

Explicit `python` and explicit `rust` preserve their R21 semantics. General
commands outside `engine route` continue to use Python under `auto`.

## Supported platform pairs

- Linux x86_64
- Windows x86_64
- macOS x86_64
- macOS aarch64

Unknown systems or architectures select Python before candidate execution.

## Consequences

- Python remains the reference and default engine.
- Rust remains experimental and read-only.
- Automatic Rust authority is limited to one verified invocation at a time.
- Existing route preflight ordering remains unchanged.
- No mutating authority, MCP authority, process execution, database migration,
  recovery or installer mutation is introduced.
- Selection provenance is visible through the existing selection envelope with
  `requested=auto`, the resolved engine, the R22 policy ID and a stable reason.

## Claim boundary

R22 may claim:

`RUST_ROUTE_SCOPED_CAPABILITY_AWARE_AUTO_R22`

R22 does not claim that Rust is the global default, production-stable or safe
for unlisted commands.
