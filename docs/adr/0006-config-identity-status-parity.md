# ADR 0006: R6 Config, Identity, and Status Parity

## Status

Accepted for the experimental Rust engine.

## Context

R5 proved byte-exact SHA-256, repository path, and manifest canonicalization parity. The next safe migration boundary is read-only configuration interpretation and status reporting. General command routing, state mutation, MCP execution, and process execution remain outside the proven Rust surface.

The Python runtime already defines:

- built-in, user, project, environment, session, and task precedence;
- per-value provenance;
- canonical JSON config hashing;
- last-good fallback after invalid current configuration;
- locked product identity at `0.0.1 / pre-release`;
- fail-closed secret-reference validation.

## Decision

R6 adds a deterministic, scalar-leaf configuration contract shared through fixtures and a compact hexadecimal wire format.

The Rust engine may expose only these new read-only diagnostics:

- `config resolve <config-wire-hex>`;
- `status [config-wire-hex]`.

The contract requires:

1. precedence order `default < user < project < environment < session < task`;
2. deterministic provenance ordering and source labels;
3. the same canonical nested JSON and SHA-256 config hash as Python;
4. environment credential-reference redaction in provenance without altering the resolved value;
5. last-good fallback with the warning `invalid-current-config-fell-back:ConfigError`;
6. exact product identity and status fields;
7. no filesystem, database, network, MCP, or process mutation.

The wire format is diagnostic and versioned as `R6CFG1`. It carries scalar leaf assignments only. Object replacement, list values, live file watching, and shared persisted state remain unproven.

## Consequences

- Python remains the reference engine and default selection.
- Rust can prove config interpretation without opening Python state stores.
- CI can compare full resolved values, provenance, hashes, warnings, and status objects.
- The capability surface expands with `config.resolve` and `status`, both marked preview and read-only.
- General Rust command routing remains blocked.
- State compatibility, MCP parity, and process parity remain not proven.
