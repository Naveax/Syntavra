# ADR 0035: Full Dual-Engine Product Runtime

Status: accepted for implementation

## Context

Syntavra currently has a complete Python product surface and several independent Rust implementations. The existing R23-R37 catalog certified a bounded set of 33 features, but the exported Python public surface contains 194 modules and 257 CLI command paths while the normal Rust CLI exposes only 10 directly equivalent public command paths. Seventeen additional routes are reached through a Python launcher.

That scoped certification must not be interpreted as a complete native rewrite of the product.

## Decision

Syntavra will be delivered as one dual-engine installation containing:

1. the complete Python runtime;
2. the complete native Rust runtime;
3. a native engine selector/launcher;
4. shared versioned contracts, state layouts and receipts;
5. no hidden fallback in either direction.

The user may select `python`, `rust` or `auto` at command, environment, project or user scope. Explicit `python` and explicit `rust` are fail-closed: a selected engine may not invoke or silently fall back to the other engine. `auto` is an explicit routing policy, not a fallback.

## Full-parity acceptance

The claim `FULL_DUAL_ENGINE_PARITY_PROVEN` is forbidden until all of the following are true:

- every exported Python public command path has an independent native Rust handler;
- every command option, output, error and mutation contract has differential fixtures;
- every MCP tool and host integration has independent Python and Rust implementations;
- both engines can read and mutate the same versioned state and SQLite layouts;
- installation artifacts contain both engines and the selector on Linux x64, Windows x64, macOS x64 and macOS arm64;
- Python-only execution succeeds with Rust binaries unavailable;
- Rust-only execution succeeds with Python unavailable;
- explicit engine selection never falls back;
- upgrade, rollback, crash-recovery, security and soak suites pass for both engines.

Top-level command names are insufficient. Coverage is measured against the complete command path inventory in `contracts/engine/dual-engine-public-surface-v2.json`.

## Packaging layout

A dual-engine bundle uses this logical layout:

```text
syntavra/
  bin/syntavra                 # native selector
  bin/syntavra-rs              # native user CLI
  bin/syntavra-full-parity     # native contract runtime during migration
  python/syntavra_runtime.whl  # Python engine
  contracts/
  bundle-manifest.json
```

The selector resolves engine precedence as:

```text
--engine
SYNTAVRA_ENGINE
project .syntavra/engine.json
user engine.json
auto policy
```

## Migration rule

Each migrated command changes from `PYTHON_ONLY` to `RUST_NATIVE_PUBLIC` only after:

- Rust implementation exists;
- the normal Rust CLI exposes the same command path;
- Python/Rust differential tests pass;
- negative and mutation-state fixtures pass;
- all target platforms pass.

`RUST_VIA_PYTHON_LAUNCHER` is transitional and never counts as native Rust coverage.

## Current baseline

At adoption of this ADR:

- Python modules: 194
- Python public command paths: 257
- direct native Rust public command paths: 10
- Python-launcher Rust bridge paths: 17
- full dual-engine claim: `DUAL_ENGINE_PARITY_INCOMPLETE`

The historical R23-R37 evidence remains valid for its bounded contracts, but it does not satisfy this ADR's full-product acceptance criteria.
