# ADR 0027: Canonical R24 config.show parity

## Status

Accepted for the R24 read-only CLI workstream.

## Context

The legacy Python `config show` command called `ConfigManager.load()` and emitted `ConfigSnapshot.to_dict()`. That path wrote `config-last-good.json`, could consume last-good fallback state, and included `loaded_at`, an invocation-time timestamp. The timestamp made repeated output differ even when configuration was unchanged and could not be reproduced by an independent Rust engine.

R24 already has bounded live configuration discovery and a canonical `R6CFG1` representation that writes no product state.

## Decision

Add the native Rust capability and command:

```text
syntavra-rs config show <R6CFG1-wire-hex>
```

The public route:

1. Discovers one immutable live configuration wire without state mutation.
2. Resolves the Python canonical snapshot.
3. Selects the route-scoped engine through `config.show`.
4. Executes native Rust `config show` on the same wire when Rust is selected.
5. Requires complete equality across the five canonical fields.
6. Never retries in Python after Rust starts.

The canonical result contains exactly:

- `schema_version`
- `values`
- `provenance`
- `config_hash`
- `warnings`

`loaded_at` is intentionally removed because it describes command invocation time rather than configuration state. Configuration values, provenance order, hashing and warning semantics are unchanged. File provenance uses canonical logical sources and environment credential-reference provenance remains `[secret-ref]`.

## Consequences

- Repeated `config show` calls over unchanged inputs produce identical output.
- The command no longer writes or reads last-good product state.
- Python and Rust can prove exact output parity.
- Consumers relying on `loaded_at` must treat its removal as the documented R24 canonicalization boundary.
- Complete R24 read-only CLI parity and full Python/Rust parity remain unclaimed.
