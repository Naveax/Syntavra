# ADR 0013 — Bounded explicit configuration wire routing

Status: accepted for R13 implementation

## Context

R6 proved deterministic Python/Rust configuration and status parity over the canonical `R6CFG1` fixture wire. R12 admitted installed `status` routing, but only for the built-in default configuration. Reading project, user or process configuration independently inside the Rust route would duplicate discovery semantics and could cause the two engines to observe different inputs.

The next boundary therefore needs a single immutable input that both engines resolve independently, without granting Rust filesystem access or exposing configuration contents in routing envelopes.

## Decision

R13 introduces an optional explicit status input:

```text
syntavra --engine python engine route status --config-wire-hex <hex>
syntavra --engine rust engine route status --config-wire-hex <hex>
```

The input must decode to a canonical `R6CFG1` wire no larger than 262,144 bytes. Python parses and resolves the wire independently. Rust receives the same normalized lower-case hexadecimal bytes as the second argument to `status`. Complete status result objects must match exactly.

The default R12 form remains available:

```text
syntavra --engine rust engine route status
```

## Canonical-input requirements

The Python reference decoder rejects:

- malformed or odd-length hexadecimal input;
- non-UTF-8 wire data;
- unknown headers, scopes or scalar types;
- non-sequential phases or scope-order drift;
- duplicate assignments and path collisions;
- invalid environment source mappings;
- non-canonical encodings;
- inputs above the fixed byte limit;
- first-phase configuration validation failures.

Later invalid phases retain the established last-good fallback semantics from R6.

## Envelope evolution

The installed routing envelope advances to schema version 2 and adds an `input` object containing only:

```text
profile
format
bytes
sha256
```

Raw wire bytes and hexadecimal input are forbidden in success and error envelopes. Arbitrary Rust execution error messages are redacted so a failing candidate binary cannot reflect the input.

## Failure policy

All R13 routes retain:

```text
fallback.policy = none
fallback.attempted = false
```

Input validation completes before engine selection or Rust execution. Invalid input, unsupported route/input combinations, candidate unavailability, execution failure, result-shape drift and status parity drift fail closed. A Rust route is never re-executed in Python.

## Consequences

- Installed `status` routing supports deterministic default and explicit canonical configuration inputs.
- Both engines consume the same immutable wire, eliminating configuration discovery races inside the route.
- Rust still cannot read configuration files, environment variables or state as part of installed status routing.
- `version` rejects configuration input.
- `config.resolve` remains outside the installed route whitelist.
- Rust receives no mutation, migration, recovery, MCP or installer authority.

## Claim boundary

```text
RUST_READ_ONLY_VERSION_ROUTING_PARITY_PROVEN_R11
RUST_READ_ONLY_STATUS_ROUTING_PARITY_PROVEN_R12
RUST_EXPLICIT_CONFIG_STATUS_ROUTING_PARITY_PROVEN_R13
RUST_AUTOMATIC_LIVE_CONFIG_DISCOVERY_NOT_PROVEN
RUST_GENERAL_PRODUCT_COMMAND_ROUTING_NOT_PROVEN
RUST_MUTATING_COMMAND_ROUTING_NOT_PROVEN
```
