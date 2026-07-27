# ADR 0004: R4 engine selector remains Python-default and fail-closed

- Status: accepted
- Phase: R4
- Product: Syntavra 0.0.1 / pre-release

## Context

R0-R3 established the Rust bootstrap and cross-language contracts without changing the existing Python product path. R4 needs a real user-visible selection surface before any production command is routed to Rust.

The selector must not imply parity that has not been proven. Rust currently exposes only product identity, capability discovery and contract hashing. Existing Syntavra commands may mutate project or runtime state, so silently executing them in Python after a Rust selection would violate the fail-closed architecture.

## Decision

The installed `syntavra` entry point is wrapped by a selector boundary with this precedence:

```text
--engine
> SYNTAVRA_ENGINE
> .syntavra/engine.json
> user engine.json
> builtin default
```

Accepted values are `auto`, `python` and `rust`.

R4 policy is:

- `python` selects the maintained reference implementation;
- `auto` resolves to Python;
- `rust` may be persisted only when the native binary passes identity, capability and contract-hash verification;
- general commands are blocked when Rust is selected;
- there is no hidden fallback from Rust to Python;
- unknown values, schemas and fields fail closed.

The selector exposes:

```text
syntavra engine list
syntavra engine status
syntavra engine use python|rust|auto
syntavra engine verify
```

Project selection is stored in `.syntavra/engine.json`. User selection is stored under the platform configuration directory. The format is governed by `contracts/engine/selection.schema.json`.

## Consequences

Users can inspect and persist engine intent without changing existing Python behavior. `auto` remains safe and deterministic. Explicit Rust selection is honest: unsupported production commands stop rather than being re-executed by Python.

R5 and later phases may add routed read-only commands only after exact parity fixtures exist. Mutating routing remains blocked until state ownership, migration, rollback and recovery gates pass.
