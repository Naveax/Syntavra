# ADR 0025: R24 config.validate read-only parity

## Status

Accepted for the R24 read-only CLI workstream.

## Context

The legacy Python `config validate` path constructed `ConfigManager`, which persisted `config-last-good.json` after a successful load and could consume that state as a fallback after an invalid load. That behavior is useful for runtime configuration continuity, but it is not a side-effect-free validation command.

The repository already has a bounded live configuration discovery contract from R15. It rejects symlinks, bounds file and override sizes, checks file identity before and after reads, emits one canonical `R6CFG1` wire, and writes no product state. The Rust engine already proves exact `config.resolve` parity for that wire format.

## Decision

The public `config validate` command is routed through a new R24 read-only boundary:

1. Python-owned live discovery creates one canonical `R6CFG1` wire without writing state.
2. Python resolves the wire and prepares the expected snapshot.
3. Engine selection occurs only after discovery and validation preflight.
4. Python selection returns the deterministic validation projection directly.
5. Rust selection executes the verified Rust `config resolve` primitive on the same wire.
6. The complete Rust snapshot must equal the Python snapshot before the validation projection is emitted.
7. Explicit Rust execution never falls back to Python after the candidate starts.

The public result remains:

```json
{
  "ok": true,
  "config_hash": "<sha256>",
  "warnings": []
}
```

## Consequences

- `config validate` is state-free and fallback-free through the public CLI entrypoint.
- No new Rust capability is invented; the route reuses the already-proven `config.resolve` capability.
- Invalid live configuration fails before engine verification or candidate execution.
- `config show` and `config explain` remain outside this decision because the legacy show result includes nondeterministic `loaded_at` and both commands require separate deterministic output contracts.
- This decision does not prove complete R24 read-only CLI parity or full Python/Rust parity.
