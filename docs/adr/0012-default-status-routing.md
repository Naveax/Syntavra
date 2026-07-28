# ADR 0012 — Default status read-only routing

Status: accepted for R12 implementation

## Context

R6 proved exact Python/Rust parity for configuration resolution and status projection across deterministic fixtures. R11 then introduced the installed read-only router with only `version` admitted. The Rust binary already exposes `status`, but direct binary capability does not automatically authorize installed routing.

Routing the full live product status immediately would require project, user, environment, session and task configuration discovery to be independently specified at the installed dispatcher boundary. That contract is not yet frozen and must not be inferred from Python implementation details.

## Decision

R12 admits a second route:

```text
syntavra --engine python engine route status
syntavra --engine rust engine route status
```

The R12 status route is intentionally restricted to the deterministic built-in configuration profile:

```text
input_profile = default-config-only
rust_argv = ["status"]
```

The Python route computes `status_projection(resolve_config_phases([{}]))`. The Rust route invokes the already-proven native `status` command without a configuration wire argument. The two results must be exactly equal, including configuration hash, warnings, routing boundary and mutation classification.

## Failure policy

The router continues to use:

```text
fallback.policy = none
fallback.attempted = false
```

A missing or incompatible Rust binary, execution failure, malformed result, unexpected field, identity drift or status parity mismatch produces a structured error and stops. Rust failures are never re-executed in Python.

`config.resolve` remains outside the installed route whitelist even though the direct Rust binary exposes that read-only capability. This proves that capability availability and route authorization remain separate controls.

## Consequences

- Installed read-only routing now covers `version` and deterministic default `status`.
- Project, user, environment, session and task configuration inputs are not read by the R12 status route.
- Existing direct product commands remain on the Python reference path.
- Rust receives no state-writing, migration, recovery, MCP or installer authority.
- A later phase must define an explicit input transport before live configuration routing can be admitted.

## Claim boundary

```text
RUST_READ_ONLY_VERSION_ROUTING_PARITY_PROVEN_R11
RUST_READ_ONLY_STATUS_ROUTING_PARITY_PROVEN_R12
RUST_LIVE_CONFIG_STATUS_ROUTING_NOT_PROVEN
RUST_GENERAL_PRODUCT_COMMAND_ROUTING_NOT_PROVEN
RUST_MUTATING_COMMAND_ROUTING_NOT_PROVEN
```
