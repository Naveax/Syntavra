# ADR 0011 — Safe read-only command routing

Status: accepted for R11 implementation

## Context

R0–R10 proved independent Python and Rust behavior for canonical primitives, configuration, identity, status, state layout, receipts, state-root inspection, quiescent broker snapshots and bounded live SQLite backup snapshots. Python remains the reference and default engine. Rust remains experimental.

The R4 selector deliberately blocked general Rust command routing. Enabling the whole Python command inventory at once would create an unbounded compatibility claim and could hide unsupported behavior behind a Python fallback.

## Decision

R11 introduces a language-independent, capability-whitelisted read-only router under:

```text
syntavra --engine python engine route version
syntavra --engine rust engine route version
```

The initial whitelist contains only `version`. The route requires the already-proven Rust `version` capability and produces the same fixed success-envelope schema for both engines.

A route is accepted only when:

1. the command is present in `contracts/engine/read-only-routing-v1.json`;
2. the selected engine resolves through the existing precedence contract;
3. an explicitly selected Rust engine passes binary, identity, capability and descriptor verification;
4. the Rust result contains exactly the required fields and locked product identity;
5. the operation is declared `read-only`;
6. response size and execution time remain bounded.

## Failure policy

Unsupported commands, unavailable Rust binaries, incompatible descriptors, execution failures and malformed results fail closed through the common JSON error envelope.

```text
fallback.policy = none
fallback.attempted = false
```

The router never re-executes a failed Rust route in Python. `auto` continues to resolve to Python. Existing direct product commands remain on the Python reference path until their individual routing contracts and parity fixtures land.

## Consequences

- The routing mechanism is proven without granting Rust mutation rights.
- `engine use rust` can be exercised against the current complete read-only capability inventory.
- Additional commands require an explicit contract row, parity verifier and fail-closed regression fixtures.
- R11 does not change the default engine, state ownership, MCP routing, database writes, migrations, recovery or installer behavior.

## Claim boundary

```text
RUST_READ_ONLY_VERSION_ROUTING_PARITY_PROVEN_R11
RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT
RUST_GENERAL_PRODUCT_COMMAND_ROUTING_NOT_PROVEN
RUST_MUTATING_COMMAND_ROUTING_NOT_PROVEN
```
