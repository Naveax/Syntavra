# ADR 0026: Native R24 config.explain parity

## Status

Accepted for the R24 read-only CLI workstream.

## Context

The legacy Python `config explain <path>` command loaded configuration through `ConfigManager`. A successful load persisted `config-last-good.json`; an invalid load could consume that state as fallback. The returned object was deterministic except that file-backed provenance exposed machine-specific absolute paths.

R24 already provides bounded live configuration discovery and a canonical `R6CFG1` wire without product-state mutation. Unlike `config.validate`, explain is an observable command projection and therefore requires a native Rust command rather than a Python-only projection over `config.resolve`.

## Decision

Add the Rust capability and command:

```text
syntavra-rs config explain <R6CFG1-wire-hex> <path-utf8-hex>
```

The public route follows this sequence:

1. Validate the path before filesystem discovery or engine verification.
2. Discover one immutable live configuration wire without writing state.
3. Resolve the Python reference snapshot and derive the expected explain result.
4. Select the route-scoped engine using the `config.explain` capability.
5. For Rust, execute the native command on the same wire and path.
6. Require complete result equality; never retry in Python after Rust starts.

Path input is limited to 512 UTF-8 bytes, forbids control characters and empty dotted segments, and uses exact case-sensitive matching against provenance paths.

The result preserves the legacy shape:

- found: `{path, value, source, scope}`
- not found: `{found: false, path}`

File-backed `source` values are normalized to the canonical logical identifiers `user-config` and `project-config` rather than machine-specific absolute paths. Environment credential references remain redacted as `[secret-ref]`.

## Consequences

- `config explain` becomes state-free, fallback-free and native in both engines.
- Rust owns the final projection instead of merely supplying a resolved snapshot.
- Output is stable across machines because filesystem paths are not exposed.
- `config show` remains outside this decision because its legacy result contains nondeterministic `loaded_at`.
- Complete R24 read-only CLI parity and full Python/Rust parity remain unclaimed.
