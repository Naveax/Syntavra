# ADR 0030 — Telemetry Metrics Read-Only CLI Parity

## Status

Accepted for R24 pre-release parity work.

## Context

`telemetry metrics` appears read-only, but the existing Python command creates an
`Observability` instance before taking the snapshot. `Observability.__init__`
creates the observability root directory, so an inspection command can create
`.syntavra/pre-release/observability` in an otherwise empty project.

The registry is process-local and newly constructed for every CLI invocation.
Therefore the observable command result is deterministic: JSON contains empty
counter, gauge and histogram arrays, while Prometheus output is empty text.

## Decision

R24 introduces route `telemetry.metrics` and capability `telemetry.metrics`.
Both engines implement the existing process-local semantics without constructing
`Observability` or touching the selected state root.

Supported public forms are:

```text
syntavra --engine python|rust|auto telemetry metrics
syntavra --engine python|rust|auto telemetry metrics --prometheus
```

The internal route uses a canonical result wrapper:

```json
{"format":"json","metrics":{"counters":[],"gauges":[],"histograms":[]}}
```

or:

```json
{"format":"prometheus","text":""}
```

The public CLI preserves the prior presentation by printing only the metrics
object for JSON and the empty Prometheus text line for `--prometheus`.

## Security and parity properties

- no state root, observability directory, event log or database is created;
- no filesystem input is read;
- Python computes the canonical result before engine selection;
- verified Rust executes exactly once for explicit Rust or eligible auto mode;
- the complete candidate result must equal the Python result;
- Rust failure or parity drift never triggers Python fallback;
- parity errors expose only mismatched top-level keys and SHA-256 digests.

## Verification

The implementation includes a language-independent contract, Python regression
fixtures, native Rust serialization, a Cargo-backed differential verifier, an
independent workflow and aggregate R0–R24 parity integration.

## Consequences

This phase proves parity only for the current process-local telemetry snapshot.
Persistent telemetry/event inspection, diagnostic bundles and telemetry writes
remain Python-owned and require separate contracts.
