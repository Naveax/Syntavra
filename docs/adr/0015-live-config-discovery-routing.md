# ADR 0015: Read-only live configuration discovery routing

## Status

Accepted for R15.

## Context

R13 and R14 prove canonical explicit `R6CFG1` transport for `status` and `config.resolve`. They intentionally require the caller to construct the wire and do not discover the live user, project, or environment configuration.

The existing `ConfigManager.load()` path is not suitable for this routing boundary because successful loads persist `config-last-good.json`. R15 must remain strictly read-only.

## Decision

Add the `live-config-discovery-v1` input profile to `status` and `config.resolve`.

The Python router:

1. reads the user and project TOML files through bounded, no-follow regular-file reads;
2. collects only environment variables beginning with `SYNTAVRA_CFG__`;
3. validates that discovered layers are scalar-leaf mappings accepted by `R6CFG1`;
4. constructs one canonical immutable `R6CFG1` wire;
5. resolves the wire in Python before engine selection;
6. gives the exact same normalized wire to Rust when Rust is selected;
7. compares the complete Rust result with the Python reference result.

The Rust binary does not read configuration files or process environment state.

## Fixed bounds

- each configuration file: at most 131,072 bytes;
- combined canonical wire: at most 262,144 bytes;
- Rust response: at most 1 MiB;
- symlinked configuration files: rejected;
- changing files during discovery: rejected;
- non-TOML, non-mapping, non-scalar-leaf values: rejected;
- raw configuration wire and discovered values: absent from routing metadata and error diagnostics.

## Consequences

Python remains the reference and default engine. `auto` remains Python. R15 grants no state write, last-good write, migration, recovery, MCP, installer, or mutation authority to Rust.
