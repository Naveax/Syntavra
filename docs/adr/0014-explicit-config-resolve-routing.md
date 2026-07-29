# ADR 0014: Explicit Config Resolve Routing

- Status: Accepted
- Phase: R14
- Product version: `0.0.1` pre-release, version locked

## Context

R13 proved that an immutable canonical `R6CFG1` configuration wire can be validated by Python, transported to the Rust candidate, and used to produce an exactly equal `status` projection. The Rust binary already exposes the direct read-only capability `config.resolve`, but installed product routing deliberately kept it outside the whitelist.

The next parity boundary must prove the complete resolved configuration snapshot, including values, provenance, configuration hash, schema version, and warnings. It must not grant either engine permission to discover project files, user files, process environment, session state, or task state independently.

## Decision

R14 admits `config.resolve` to the installed read-only router under these conditions:

1. The caller supplies `--config-wire-hex <hex>` explicitly.
2. The decoded input is canonical `R6CFG1` and is limited to 262,144 bytes.
3. Python validates and resolves the wire before engine selection.
4. Rust receives the same normalized lower-case hexadecimal wire.
5. Python and Rust must return exactly equal snapshot objects with only these top-level fields:
   - `schema_version`
   - `values`
   - `provenance`
   - `config_hash`
   - `warnings`
6. A missing wire fails before engine selection.
7. Invalid or non-canonical input fails before engine selection.
8. Rust execution failure, schema drift, snapshot drift, or oversized output fails closed without Python re-execution.
9. Route metadata records only input profile, format, byte count, and SHA-256. The raw wire is never returned.
10. Snapshot parity errors contain only mismatched top-level keys and canonical result digests. Resolved values and provenance are forbidden in parity diagnostics.
11. Successful `config.resolve` output intentionally contains resolved configuration values because that is the explicit purpose of the command.
12. Python remains the default and reference engine. Rust remains experimental and read-only.

## Command surface

```text
syntavra --engine python engine route config.resolve --config-wire-hex <hex>
syntavra --engine rust engine route config.resolve --config-wire-hex <hex>
```

The existing `version` and `status` routes remain unchanged.

## Rejected alternatives

### Automatic live configuration discovery

Rejected for R14. Independent file and environment reads would introduce path ownership, environment capture, race, secret handling, and provenance-consistency problems that explicit immutable transport avoids.

### Default configuration for `config.resolve`

Rejected. A full snapshot command must identify its input explicitly; absence of input is an error rather than an implicit default.

### Returning expected and actual snapshots in parity errors

Rejected. It would leak resolved configuration values and provenance into diagnostics. Digest-only parity evidence is sufficient.

### Fallback to Python after a Rust failure

Rejected. Hidden re-execution would make engine selection and failure semantics ambiguous.

## Consequences

R14 proves installed Python/Rust routing parity for complete deterministic configuration snapshots. It does not prove automatic live configuration export, file discovery, environment capture, MCP transport, mutation, migration, recovery, or Rust default selection.

The next live-config phase must define one authoritative exporter and an immutable transport boundary before either engine can consume real project/user/environment configuration through installed routing.
